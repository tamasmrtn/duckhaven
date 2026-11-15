"""The published dashboards a change to the semantic layer would break.

Dashboards read published models and published metrics only. So removing or
deprecating a model breaks every published dashboard that reads it, and so does
removing a metric or a dimension, or taking a metric back to draft. The
semantic routes ask :func:`gate` before making such a change, and an import
asks :func:`affected` for its dry run.

``dashboard_refs`` indexes what each file reads, so this is one query however
many dashboards there are. A draft is not counted: publishing it checks it again.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.dashboard import Dashboard, DashboardRef
from api.models.semantic import SemanticDimension, SemanticMetric, SemanticModel

#: Dashboards a refusal names in its message; ``details`` lists them all.
NAMED = 10


@dataclass
class Removal:
    """What a change takes away from dashboards: models by slug, the rest as (model, name)."""

    models: set[str] = field(default_factory=set)
    metrics: set[tuple[str, str]] = field(default_factory=set)
    dimensions: set[tuple[str, str]] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.models or self.metrics or self.dimensions)


async def affected(db: AsyncSession, workspace_id: uuid.UUID, removal: Removal) -> list[dict]:
    """The published dashboards that read something in ``removal``, by name.

    Each is ``{name, title, reads}``, where ``reads`` lists what it reads of the
    removal: a model as its slug, a metric or dimension as ``model.name``.
    """
    if not removal:
        return []
    wanted = []
    if removal.models:
        wanted.append(
            and_(DashboardRef.kind == "model", DashboardRef.name.in_(sorted(removal.models)))
        )
    for kind, pairs in (("metric", removal.metrics), ("dimension", removal.dimensions)):
        by_model: dict[str, set[str]] = defaultdict(set)
        for model, name in pairs:
            by_model[model].add(name)
        wanted += [
            and_(
                DashboardRef.kind == kind,
                DashboardRef.model == model,
                DashboardRef.name.in_(sorted(names)),
            )
            for model, names in sorted(by_model.items())
        ]
    rows = await db.execute(
        select(
            Dashboard.name,
            Dashboard.title,
            DashboardRef.kind,
            DashboardRef.model,
            DashboardRef.name,
        )
        .join(Dashboard, Dashboard.id == DashboardRef.dashboard_id)
        .where(
            DashboardRef.workspace_id == workspace_id,
            DashboardRef.state == "published",
            or_(*wanted),
        )
        .order_by(Dashboard.name, DashboardRef.model, DashboardRef.name)
    )
    found: dict[str, dict] = {}
    for name, title, kind, model, ref in rows:
        entry = found.setdefault(name, {"name": name, "title": title, "reads": []})
        entry["reads"].append(ref if kind == "model" else f"{model}.{ref}")
    return list(found.values())


def refusal(change: str, dashboards: list[dict]) -> HTTPException:
    """A 409 ``breaking_dashboards``, naming the dashboards ``change`` would break."""
    names = ", ".join(d["name"] for d in dashboards[:NAMED])
    if len(dashboards) > NAMED:
        names += f" and {len(dashboards) - NAMED} more"
    count = f"{len(dashboards)} published dashboard{'s' if len(dashboards) != 1 else ''}"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "breaking_dashboards",
            "detail": (
                f"{change} breaks {count}: {names}. Change them first, or pass "
                "allow_breaking=true to make the change anyway."
            ),
            "affected_dashboards": dashboards,
        },
    )


async def gate(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    removal: Removal,
    *,
    change: str,
    allow: bool,
) -> list[dict]:
    """Refuse ``change`` while it breaks a published dashboard, unless ``allow``.

    Returns the dashboards it breaks, which is empty unless ``allow`` is set.
    """
    found = await affected(db, workspace_id, removal)
    if found and not allow:
        raise refusal(change, found)
    return found


async def members(
    db: AsyncSession, workspace_id: uuid.UUID, *, provider: str
) -> dict[str, tuple[set[str], set[str]]]:
    """Each of ``provider``'s models, by slug, with its published metrics and its dimensions.

    Read before and after an import, the difference is what the import removes.
    """
    models = {
        slug: (set(), set())
        for slug in (
            await db.execute(
                select(SemanticModel.slug).where(
                    SemanticModel.workspace_id == workspace_id,
                    SemanticModel.provider == provider,
                )
            )
        ).scalars()
    }
    for table, index, extra in (
        (SemanticMetric, 0, SemanticMetric.status == "published"),
        (SemanticDimension, 1, true()),
    ):
        rows = await db.execute(
            select(SemanticModel.slug, table.name)
            .join(SemanticModel, SemanticModel.id == table.model_id)
            .where(
                SemanticModel.workspace_id == workspace_id,
                SemanticModel.provider == provider,
                extra,
            )
        )
        for slug, name in rows:
            models[slug][index].add(name)
    return models


def removed(
    before: dict[str, tuple[set[str], set[str]]], after: dict[str, tuple[set[str], set[str]]]
) -> Removal:
    """What going from ``before`` to ``after``, as :func:`members` reads them, removes."""
    removal = Removal()
    for slug, (metrics, dimensions) in before.items():
        if slug not in after:
            removal.models.add(slug)
            continue
        kept_metrics, kept_dimensions = after[slug]
        removal.metrics |= {(slug, m) for m in metrics - kept_metrics}
        removal.dimensions |= {(slug, d) for d in dimensions - kept_dimensions}
    return removal
