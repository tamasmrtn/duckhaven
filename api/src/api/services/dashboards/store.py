"""Storing dashboards: the canonical file, its hash, what it reads, names and the write guard.

Every write to a dashboard, from the REST API, the assistant, MCP or an accepted
proposal, goes through :func:`assert_writable` first. A dashboard whose file
lives in git is changed only by applying that file, so the repository stays the
one place it is edited.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import yaml
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.dashboard import Dashboard, DashboardAlias, DashboardRef
from api.services.dashboards.spec import DashboardSpec, DimensionParameter, TextWidget
from api.services.grants import GrantDenied, extract_table_refs

#: A ref: (kind, model, name). ``model`` is set for metric and dimension refs.
Ref = tuple[str, str | None, str]


def stored(spec: DashboardSpec) -> dict:
    """The file as a JSON column holds it: every key it sets, no defaults."""
    return spec.model_dump(mode="json", exclude_defaults=True)


def read(data: dict) -> DashboardSpec:
    """A stored file, read back. Stored files were validated when they were saved."""
    return DashboardSpec.model_validate(data)


def spec_hash(spec: DashboardSpec) -> str:
    """The sha256 of the file's canonical JSON. Equal files hash equal, however written."""
    canonical = json.dumps(stored(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class _Dumper(yaml.SafeDumper):
    """Block lists indented under their key, scalar lists inline, multi-line text as ``|``."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def _list(dumper: yaml.SafeDumper, data: list):
    inline = all(not isinstance(v, (dict, list)) for v in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=inline)


def _str(dumper: yaml.SafeDumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(list, _list)
_Dumper.add_representer(str, _str)


def export(spec: DashboardSpec) -> str:
    """The file as canonical YAML, which :func:`~loader.load` reads back to the same spec."""
    data = stored(spec)
    for row in data["rows"]:
        # A widget reads best starting with what it is.
        row["widgets"] = [{"id": w.pop("id"), "type": w.pop("type"), **w} for w in row["widgets"]]
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def refs(spec: DashboardSpec) -> list[Ref]:
    """The models, metrics, dimensions and tables ``spec`` reads, sorted."""
    found: set[Ref] = set()
    for param in spec.parameters.values():
        if isinstance(param, DimensionParameter):
            found |= {("model", None, param.model), ("dimension", param.model, param.dimension)}
    for _, widget in spec.widgets():
        if isinstance(widget, TextWidget):
            continue
        if widget.query is not None:
            query = widget.query
            found.add(("model", None, query.model))
            found |= {("metric", query.model, m) for m in query.metrics}
            dimensions = [*query.dimensions, *(f.dimension for f in query.filters)]
            found |= {("dimension", query.model, d) for d in dimensions}
            continue
        try:
            tables = extract_table_refs(widget.sql)
        except GrantDenied:
            continue  # unparseable; validation reports it
        for ref in tables:
            name = ".".join(p for p in (ref.catalog, ref.schema, ref.table) if p)
            found.add(("table", None, name))
    return sorted(found, key=lambda r: (r[0], r[1] or "", r[2]))


async def set_refs(
    db: AsyncSession, dashboard: Dashboard, state: str, spec: DashboardSpec | None
) -> None:
    """Replace ``dashboard``'s refs for ``state`` with what ``spec`` reads."""
    await db.execute(
        delete(DashboardRef).where(
            DashboardRef.dashboard_id == dashboard.id, DashboardRef.state == state
        )
    )
    if spec is None:
        return
    for kind, model, name in refs(spec):
        db.add(
            DashboardRef(
                dashboard_id=dashboard.id,
                workspace_id=dashboard.workspace_id,
                state=state,
                kind=kind,
                model=model,
                name=name,
            )
        )


async def find(db: AsyncSession, workspace_id: uuid.UUID, name: str) -> Dashboard | None:
    """The dashboard called ``name``, or the one that was called that before a rename."""
    live = (
        await db.execute(
            select(Dashboard).where(
                Dashboard.workspace_id == workspace_id,
                func.lower(Dashboard.name) == name.lower(),
            )
        )
    ).scalar_one_or_none()
    if live is not None:
        return live
    return (
        await db.execute(
            select(Dashboard)
            .join(DashboardAlias, DashboardAlias.dashboard_id == Dashboard.id)
            .where(
                DashboardAlias.workspace_id == workspace_id,
                func.lower(DashboardAlias.name) == name.lower(),
            )
        )
    ).scalar_one_or_none()


async def assert_name_free(
    db: AsyncSession, workspace_id: uuid.UUID, name: str, *, mine: uuid.UUID | None = None
) -> None:
    """Refuse ``name`` when another dashboard has it. An alias does not count."""
    taken = (
        await db.execute(
            select(Dashboard.id).where(
                Dashboard.workspace_id == workspace_id,
                func.lower(Dashboard.name) == name.lower(),
            )
        )
    ).scalar_one_or_none()
    if taken is not None and taken != mine:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dashboard_name_taken",
                "detail": f"This workspace already has a dashboard called {name!r}.",
            },
        )


async def claim_name(db: AsyncSession, dashboard: Dashboard, name: str) -> None:
    """Give ``dashboard`` the name ``name``, keeping its old one as an alias."""
    if dashboard.name == name:
        return
    await assert_name_free(db, dashboard.workspace_id, name, mine=dashboard.id)
    await db.execute(
        delete(DashboardAlias).where(
            DashboardAlias.workspace_id == dashboard.workspace_id,
            func.lower(DashboardAlias.name) == name.lower(),
        )
    )
    if dashboard.name and dashboard.name.lower() != name.lower():
        db.add(
            DashboardAlias(
                workspace_id=dashboard.workspace_id,
                dashboard_id=dashboard.id,
                name=dashboard.name,
            )
        )
    dashboard.name = name


def assert_writable(dashboard: Dashboard) -> None:
    """Refuse a change to a dashboard whose file lives in git."""
    if dashboard.source == "git":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "managed_dashboard",
                "detail": (
                    f"{dashboard.name!r} is managed in git"
                    + (f" ({dashboard.source_id})" if dashboard.source_id else "")
                    + ". Change its file there and apply it again."
                ),
            },
        )
