"""Checking a dashboard against the semantic layer, as the person asking.

The loader checks a file's shape and its references to its own parameters. This
checks what only the workspace can answer: that each model is published and
visible to the caller, each dimension parameter names a real dimension, each
query widget compiles with the parameters' defaults, and each SQL widget is one
read-only statement. A draft may fail these; publishing may not.

It runs as the caller, so a model they cannot see reads as missing, exactly as
it would on the page. SQL widgets are not checked against grants here: each
viewer's grants apply when the widget runs.
"""

from __future__ import annotations

import uuid
from datetime import date

import duckdb
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.services import sql_guard
from api.services.dashboards.loader import Diagnostic, Loaded
from api.services.dashboards.resolve import resolve
from api.services.dashboards.spec import DimensionParameter, TextWidget
from api.services.grants import GrantDenied, extract_table_refs
from api.services.semantic import store as semantic_store
from api.services.semantic.compile import compile_metric_query, legal_dimensions
from api.services.semantic.errors import SemanticError
from api.services.semantic.model import LoadedModel, load_model


async def problems(
    db: AsyncSession,
    loaded: Loaded,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    catalogs: list[Catalog],
    today: date,
) -> list[Diagnostic]:
    """Every fault the workspace finds in ``loaded``, in file order. Empty when it can publish."""
    spec = loaded.spec
    widgets = [(path, w) for path, w in spec.widgets() if not isinstance(w, TextWidget)]
    slugs = {p.model for p in spec.parameters.values() if isinstance(p, DimensionParameter)}
    slugs |= {w.query.model for _, w in widgets if w.query is not None}

    models: dict[str, LoadedModel] = {}
    missing: dict[str, str] = {}
    for slug in sorted(slugs):
        try:
            row = await semantic_store.get_model(
                db, workspace_id=workspace_id, slug=slug, user_id=user_id, catalogs=catalogs
            )
        except semantic_store.SemanticNotFound:
            missing[slug] = f"There is no semantic model {slug!r}."
            continue
        if row.status != "published":
            missing[slug] = (
                f"Semantic model {slug!r} is {row.status}; dashboards read published models only."
            )
            continue
        models[slug] = await load_model(db, row)

    found: list[Diagnostic] = []
    for name, param in spec.parameters.items():
        if not isinstance(param, DimensionParameter):
            continue
        if param.model in missing:
            found.append(loaded.at(f"parameters.{name}.model", missing[param.model]))
            continue
        model = models[param.model]
        if param.dimension not in model.dimensions:
            found.append(
                loaded.at(
                    f"parameters.{name}.dimension",
                    model.why_missing("dimension", param.dimension)
                    or str(
                        SemanticError(
                            f"{param.model!r} has no dimension {param.dimension!r}.",
                            alternatives=sorted(model.dimensions),
                        )
                    ),
                )
            )

    def reachable(slug: str, metric: str) -> list[str]:
        try:
            return legal_dimensions(models[slug], metric) if slug in models else []
        except SemanticError:
            return []  # the widget's own compile names the metric

    resolved = resolve(spec, {}, today=today, reachable=reachable)
    for path, widget in widgets:
        run = resolved.widgets[widget.id]
        if run.error:
            found.append(loaded.at(path, run.error))
        elif widget.query is not None:
            if widget.query.model in missing:
                found.append(loaded.at(f"{path}.query.model", missing[widget.query.model]))
                continue
            try:
                compile_metric_query(models[widget.query.model], run.query, today=today)
            except SemanticError as exc:
                found.append(loaded.at(f"{path}.query", str(exc)))
        else:
            message = _sql_problem(widget.sql)
            if message:
                found.append(loaded.at(f"{path}.sql", message))
    return found


def _sql_problem(sql: str) -> str | None:
    try:
        statements = duckdb.extract_statements(sql)
    except Exception as exc:  # DuckDB's parser errors share no base class worth naming
        return f"SQL parse error: {exc}"
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        return "An SQL widget runs one SELECT statement."
    try:
        sql_guard.assert_allowed(sql)
        extract_table_refs(sql)
    except (sql_guard.SQLNotAllowed, GrantDenied) as exc:
        return str(exc)
    return None
