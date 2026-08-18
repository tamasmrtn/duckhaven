"""The semantic layer's HTTP surface: define, validate, publish, search, compile.

Three access levels, matching what each action can do wrong. Reading needs
workspace ``reader``. Authoring needs ``writer`` — a draft is somebody's working
notes and cannot mislead anyone yet. **Publishing needs ``owner``**, because
publishing is what makes a definition authoritative to the assistant, and the
minimum useful governance is exactly the ability to stop an experiment being
quoted as fact.

The compile endpoint deliberately does *not* execute. It returns SQL, and the
caller submits that SQL through the ordinary ``POST /queries`` route. That keeps
one execution path, one grant check and one audit trail — the semantic layer adds
no way to run a query, so there is no second way to get authorization wrong. It
also means "show me the SQL this metric produces" is the same call the assistant
makes, rather than a separate approximation of it.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.catalog import Catalog
from api.models.semantic import (
    NATIVE_PROVIDER,
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)
from api.models.user import User
from api.schemas.semantic import (
    CompiledQueryOut,
    DatasetIn,
    DatasetOut,
    DependentOut,
    DimensionIn,
    DimensionOut,
    MetricIn,
    MetricOut,
    MetricQueryIn,
    MetricUpdate,
    ModelIn,
    ModelOut,
    ModelSummaryOut,
    ModelUpdate,
    RelationshipIn,
    RelationshipOut,
    SemanticImportOut,
    SemanticSearchOut,
    TableSemanticsOut,
    ValidationReportOut,
)
from api.services import grants as grant_service
from api.services.lineage.resolve import Resolver
from api.services.polaris import PolarisClient
from api.services.semantic import store
from api.services.semantic.compile import (
    DimensionFilter,
    MetricQuery,
    OrderTerm,
    compile_metric_query,
    legal_dimensions,
)
from api.services.semantic.errors import SemanticError
from api.services.semantic.impact import dependents_for_table
from api.services.semantic.ingest import (
    purge_provider,
    reconcile_provider_run,
    upsert_models,
)
from api.services.semantic.model import load_model
from api.services.semantic.providers import get_adapter
from api.services.semantic.providers.native import SemanticDocumentError
from api.services.semantic.retrieve import ambiguous, search, search_broken
from api.services.semantic.timespec import TimeRange
from api.services.semantic.validate import validate_model
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_catalog,
    resolve_workspace_catalogs,
)

router = APIRouter()


# ── Request context ───────────────────────────────────────────────────────────


class _Context:
    def __init__(self, workspace, user: User, catalogs: list[Catalog]) -> None:
        self.workspace = workspace
        self.user = user
        self.catalogs = catalogs


def _context(min_role: str):
    async def _dep(
        ws: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> _Context:
        workspace = await get_workspace(db, ws)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        await assert_workspace_member(db, workspace.id, user.id, min_role=min_role)
        catalogs = await resolve_workspace_catalogs(db, workspace.id)
        return _Context(workspace, user, catalogs)

    return _dep


def _not_found(slug: str) -> HTTPException:
    # 404 rather than 403, matching `enforce_leaf`: a model the caller may not
    # see must be indistinguishable from one that does not exist.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"No semantic model {slug!r}."
    )


def _semantic_error(exc: SemanticError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": "semantic_error", "detail": str(exc)},
    )


def _immutable(model: SemanticModel) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "imported_model",
            "detail": (
                f"{model.slug!r} was imported from {model.provider!r} and is edited there, "
                "not here. Change it at the source and import again."
            ),
        },
    )


async def _editable(db: AsyncSession, ctx: _Context, slug: str) -> SemanticModel:
    """Fetch a model for writing, refusing anything an import owns."""
    try:
        model = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc
    if model.provider != NATIVE_PROVIDER:
        raise _immutable(model)
    model.updated_at = datetime.now(UTC)
    return model


# ── Serialisation ─────────────────────────────────────────────────────────────


def _summary(model: SemanticModel, datasets, dimensions, metrics) -> ModelSummaryOut:
    broken = sum(
        1 for item in (*datasets, *dimensions, *metrics) if item.validation_state == "broken"
    )
    return ModelSummaryOut(
        id=model.id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        status=model.status,
        provider=model.provider,
        owner_id=model.owner_id,
        metric_count=len(metrics),
        dimension_count=len(dimensions),
        dataset_count=len(datasets),
        broken_count=broken,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _render(metric: SemanticMetric) -> str:
    inner = metric.expr or "*"
    call = (
        f"COUNT(DISTINCT {inner})"
        if metric.agg == "count_distinct"
        else f"{metric.agg.upper()}({inner})"
    )
    if metric.filter:
        call = f"{call} FILTER (WHERE {metric.filter})"
    return call


def _full(
    model: SemanticModel,
    datasets: list[SemanticDataset],
    dimensions: list[SemanticDimension],
    metrics: list[SemanticMetric],
    relationships: list[SemanticRelationship],
    catalog_slugs: dict[uuid.UUID, str],
) -> ModelOut:
    ds_names = {d.id: d.name for d in datasets}
    dim_names = {d.id: d.name for d in dimensions}
    summary = _summary(model, datasets, dimensions, metrics)
    return ModelOut(
        **summary.model_dump(),
        datasets=[
            DatasetOut(
                id=d.id,
                name=d.name,
                description=d.description,
                synonyms=d.synonyms or [],
                catalog=catalog_slugs.get(d.catalog_id),
                schema_name=d.schema_name,
                table_name=d.table_name,
                primary_key=d.primary_key or [],
                validation_state=d.validation_state,
                validation_detail=d.validation_detail,
            )
            for d in datasets
        ],
        dimensions=[
            DimensionOut(
                id=d.id,
                name=d.name,
                dataset=ds_names.get(d.dataset_id),
                display_name=d.display_name,
                description=d.description,
                synonyms=d.synonyms or [],
                kind=d.kind,
                expr=d.expr,
                data_type=d.data_type,
                time_grains=d.time_grains or [],
                is_default_time=d.is_default_time,
                sample_values=d.sample_values or [],
                validation_state=d.validation_state,
                validation_detail=d.validation_detail,
            )
            for d in dimensions
        ],
        metrics=[
            MetricOut(
                id=m.id,
                name=m.name,
                dataset=ds_names.get(m.dataset_id),
                display_name=m.display_name,
                description=m.description,
                synonyms=m.synonyms or [],
                agg=m.agg,
                expr=m.expr,
                filter=m.filter,
                time_dimension=dim_names.get(m.time_dimension_id) if m.time_dimension_id else None,
                caveat=m.caveat,
                status=m.status,
                expression=_render(m),
                validation_state=m.validation_state,
                validation_detail=m.validation_detail,
            )
            for m in metrics
        ],
        relationships=[
            RelationshipOut(
                id=r.id,
                name=r.name,
                left_dataset=ds_names.get(r.left_dataset_id),
                right_dataset=ds_names.get(r.right_dataset_id),
                join_columns=r.join_columns or [],
                cardinality=r.cardinality,
                validation_state=r.validation_state,
                validation_detail=r.validation_detail,
            )
            for r in relationships
        ],
    )


async def _catalog_slugs(ctx: _Context) -> dict[uuid.UUID, str]:
    return {c.id: c.slug for c in ctx.catalogs}


# ── Models ────────────────────────────────────────────────────────────────────


@router.get("/workspaces/{ws}/semantic/models", response_model=list[ModelSummaryOut])
async def list_models(
    status_filter: str | None = Query(default=None, alias="status"),
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> list[ModelSummaryOut]:
    rows = await store.list_models(
        db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        catalogs=ctx.catalogs,
        status=status_filter,
    )
    out: list[ModelSummaryOut] = []
    for row in rows:
        datasets, dimensions, metrics, _ = await store.children(db, row.id)
        out.append(_summary(row, datasets, dimensions, metrics))
    return out


@router.post(
    "/workspaces/{ws}/semantic/models",
    response_model=ModelOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    body: ModelIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> ModelOut:
    existing = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.workspace_id == ctx.workspace.id,
                SemanticModel.slug == body.slug,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A semantic model called {body.slug!r} already exists.",
        )
    model = SemanticModel(
        workspace_id=ctx.workspace.id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        owner_id=ctx.user.id,
        provider=NATIVE_PROVIDER,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return _full(model, [], [], [], [], await _catalog_slugs(ctx))


@router.get("/workspaces/{ws}/semantic/models/{slug}", response_model=ModelOut)
async def get_model(
    slug: str,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> ModelOut:
    try:
        model = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc
    datasets, dimensions, metrics, relationships = await store.children(db, model.id)
    return _full(model, datasets, dimensions, metrics, relationships, await _catalog_slugs(ctx))


@router.patch("/workspaces/{ws}/semantic/models/{slug}", response_model=ModelOut)
async def update_model(
    slug: str,
    body: ModelUpdate,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> ModelOut:
    model = await _editable(db, ctx, slug)
    if body.name is not None:
        model.name = body.name
    if body.description is not None:
        model.description = body.description
    await db.commit()
    datasets, dimensions, metrics, relationships = await store.children(db, model.id)
    return _full(model, datasets, dimensions, metrics, relationships, await _catalog_slugs(ctx))


@router.delete("/workspaces/{ws}/semantic/models/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    slug: str,
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        model = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc
    await db.delete(model)
    await db.commit()


@router.post("/workspaces/{ws}/semantic/models/{slug}/publish", response_model=ModelOut)
async def publish_model(
    slug: str,
    polaris: PolarisClient = Depends(get_polaris_client),
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> ModelOut:
    """Make a model authoritative to the assistant.

    Validated first, and refused if anything is broken. Publishing is precisely
    the moment a definition stops being one person's draft and starts being
    quoted as fact, so it is the right place to insist the bindings actually hold.
    """
    try:
        model = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc

    report = await validate_model(
        db, polaris, model, catalog_names={c.id: c.polaris_name for c in ctx.catalogs}
    )
    if not report.ok:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "validation_failed",
                "detail": "This model cannot be published until its definitions resolve.",
                "errors": report.errors,
            },
        )

    model.status = "published"
    model.updated_at = datetime.now(UTC)
    # Metrics that are still drafts stay drafts: publishing the model is a
    # statement about the model, not a blanket approval of everything in it.
    await db.commit()
    datasets, dimensions, metrics, relationships = await store.children(db, model.id)
    return _full(model, datasets, dimensions, metrics, relationships, await _catalog_slugs(ctx))


@router.post("/workspaces/{ws}/semantic/models/{slug}/deprecate", response_model=ModelOut)
async def deprecate_model(
    slug: str,
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> ModelOut:
    try:
        model = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc
    model.status = "deprecated"
    model.updated_at = datetime.now(UTC)
    await db.commit()
    datasets, dimensions, metrics, relationships = await store.children(db, model.id)
    return _full(model, datasets, dimensions, metrics, relationships, await _catalog_slugs(ctx))


@router.post("/workspaces/{ws}/semantic/models/{slug}/validate", response_model=ValidationReportOut)
async def validate(
    slug: str,
    polaris: PolarisClient = Depends(get_polaris_client),
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> ValidationReportOut:
    try:
        model = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc
    report = await validate_model(
        db, polaris, model, catalog_names={c.id: c.polaris_name for c in ctx.catalogs}
    )
    await db.commit()
    return ValidationReportOut(
        ok=report.ok,
        errors=report.errors,
        warnings=report.warnings,
        checked_at=report.checked_at,
    )


# ── Children ──────────────────────────────────────────────────────────────────


async def _dataset_id(db: AsyncSession, model_id: uuid.UUID, name: str) -> uuid.UUID:
    """Resolve a logical dataset name within a model, or 422 naming the problem."""
    dataset = (
        await db.execute(
            select(SemanticDataset).where(
                SemanticDataset.model_id == model_id, SemanticDataset.name == name
            )
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"This model has no dataset called {name!r}.",
        )
    return dataset.id


@router.post(
    "/workspaces/{ws}/semantic/models/{slug}/datasets",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_dataset(
    slug: str,
    body: DatasetIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DatasetOut:
    model = await _editable(db, ctx, slug)
    catalog = await resolve_catalog(db, ctx.workspace.id, body.catalog)
    # Binding a dataset names a table, so it needs the same tier reading that
    # table's metadata needs. Otherwise a writer could learn a schema by binding
    # to it and reading the validation error back.
    await grant_service.enforce_leaf(
        db,
        ctx.workspace.id,
        catalog,
        ctx.user.id,
        schema=body.schema_name,
        table=body.table_name,
        need="metadata",
    )
    dataset = SemanticDataset(
        model_id=model.id,
        name=body.name,
        description=body.description,
        synonyms=body.synonyms,
        catalog_id=catalog.id,
        schema_name=body.schema_name,
        table_name=body.table_name,
        primary_key=body.primary_key,
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return DatasetOut(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        synonyms=dataset.synonyms or [],
        catalog=catalog.slug,
        schema_name=dataset.schema_name,
        table_name=dataset.table_name,
        primary_key=dataset.primary_key or [],
        validation_state=dataset.validation_state,
        validation_detail=dataset.validation_detail,
    )


@router.post(
    "/workspaces/{ws}/semantic/models/{slug}/dimensions",
    response_model=DimensionOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_dimension(
    slug: str,
    body: DimensionIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> DimensionOut:
    model = await _editable(db, ctx, slug)
    dataset_id = await _dataset_id(db, model.id, body.dataset)
    dimension = SemanticDimension(
        model_id=model.id,
        dataset_id=dataset_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        synonyms=body.synonyms,
        kind=body.kind,
        expr=body.expr or body.name,
        data_type=body.data_type,
        time_grains=body.time_grains,
        is_default_time=body.is_default_time,
        sample_values=body.sample_values,
    )
    db.add(dimension)
    await db.commit()
    await db.refresh(dimension)
    return DimensionOut(
        id=dimension.id,
        name=dimension.name,
        dataset=body.dataset,
        display_name=dimension.display_name,
        description=dimension.description,
        synonyms=dimension.synonyms or [],
        kind=dimension.kind,
        expr=dimension.expr,
        data_type=dimension.data_type,
        time_grains=dimension.time_grains or [],
        is_default_time=dimension.is_default_time,
        sample_values=dimension.sample_values or [],
        validation_state=dimension.validation_state,
        validation_detail=dimension.validation_detail,
    )


@router.post(
    "/workspaces/{ws}/semantic/models/{slug}/metrics",
    response_model=MetricOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_metric(
    slug: str,
    body: MetricIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> MetricOut:
    model = await _editable(db, ctx, slug)
    dataset_id = await _dataset_id(db, model.id, body.dataset)

    time_dimension_id = None
    if body.time_dimension:
        row = (
            await db.execute(
                select(SemanticDimension).where(
                    SemanticDimension.model_id == model.id,
                    SemanticDimension.name == body.time_dimension,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"This model has no dimension called {body.time_dimension!r}.",
            )
        if row.kind != "time":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"{body.time_dimension!r} is not a time dimension, so a metric cannot be "
                    "measured on it."
                ),
            )
        time_dimension_id = row.id

    metric = SemanticMetric(
        model_id=model.id,
        dataset_id=dataset_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        synonyms=body.synonyms,
        agg=body.agg,
        expr=body.expr,
        filter=body.filter,
        time_dimension_id=time_dimension_id,
        caveat=body.caveat,
        owner_id=ctx.user.id,
    )
    db.add(metric)
    await db.commit()
    await db.refresh(metric)
    return MetricOut(
        id=metric.id,
        name=metric.name,
        dataset=body.dataset,
        display_name=metric.display_name,
        description=metric.description,
        synonyms=metric.synonyms or [],
        agg=metric.agg,
        expr=metric.expr,
        filter=metric.filter,
        time_dimension=body.time_dimension,
        caveat=metric.caveat,
        status=metric.status,
        expression=_render(metric),
        validation_state=metric.validation_state,
        validation_detail=metric.validation_detail,
    )


@router.patch(
    "/workspaces/{ws}/semantic/models/{slug}/metrics/{metric_name}", response_model=MetricOut
)
async def update_metric(
    slug: str,
    metric_name: str,
    body: MetricUpdate,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> MetricOut:
    model = await _editable(db, ctx, slug)
    metric = (
        await db.execute(
            select(SemanticMetric).where(
                SemanticMetric.model_id == model.id, SemanticMetric.name == metric_name
            )
        )
    ).scalar_one_or_none()
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No metric {metric_name!r}."
        )

    for attr in ("display_name", "description", "agg", "expr", "filter", "caveat", "status"):
        value = getattr(body, attr)
        if value is not None:
            setattr(metric, attr, value)
    if body.synonyms is not None:
        metric.synonyms = body.synonyms
    if body.time_dimension is not None:
        dim = (
            await db.execute(
                select(SemanticDimension).where(
                    SemanticDimension.model_id == model.id,
                    SemanticDimension.name == body.time_dimension,
                )
            )
        ).scalar_one_or_none()
        if dim is None or dim.kind != "time":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{body.time_dimension!r} is not a time dimension in this model.",
            )
        metric.time_dimension_id = dim.id

    # Any edit invalidates the previous verdict. Leaving it "ok" would let an
    # expression that no longer resolves keep answering questions.
    metric.validation_state = "unchecked"
    metric.validation_detail = None
    metric.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(metric)

    dim_name = None
    if metric.time_dimension_id:
        dim = (
            await db.execute(
                select(SemanticDimension).where(SemanticDimension.id == metric.time_dimension_id)
            )
        ).scalar_one_or_none()
        dim_name = dim.name if dim else None

    return MetricOut(
        id=metric.id,
        name=metric.name,
        dataset=None,
        display_name=metric.display_name,
        description=metric.description,
        synonyms=metric.synonyms or [],
        agg=metric.agg,
        expr=metric.expr,
        filter=metric.filter,
        time_dimension=dim_name,
        caveat=metric.caveat,
        status=metric.status,
        expression=_render(metric),
        validation_state=metric.validation_state,
        validation_detail=metric.validation_detail,
    )


@router.post(
    "/workspaces/{ws}/semantic/models/{slug}/relationships",
    response_model=RelationshipOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_relationship(
    slug: str,
    body: RelationshipIn,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> RelationshipOut:
    model = await _editable(db, ctx, slug)
    left = await _dataset_id(db, model.id, body.left_dataset)
    right = await _dataset_id(db, model.id, body.right_dataset)
    if left == right:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A relationship must join two different datasets.",
        )
    relationship = SemanticRelationship(
        model_id=model.id,
        name=body.name,
        left_dataset_id=left,
        right_dataset_id=right,
        join_columns=[c.model_dump() for c in body.join_columns],
        cardinality=body.cardinality,
    )
    db.add(relationship)
    await db.commit()
    await db.refresh(relationship)
    return RelationshipOut(
        id=relationship.id,
        name=relationship.name,
        left_dataset=body.left_dataset,
        right_dataset=body.right_dataset,
        join_columns=relationship.join_columns or [],
        cardinality=relationship.cardinality,
        validation_state=relationship.validation_state,
        validation_detail=relationship.validation_detail,
    )


# ── Removing children ─────────────────────────────────────────────────────────
#
# Deleting a definition is the ordinary way to fix a mistake, so it needs no more
# ceremony than adding one: `writer`, and refused on an imported model like every
# other edit. The one asymmetry is the dataset, below.


async def _child(db: AsyncSession, model_id: uuid.UUID, table, name: str, kind: str):
    obj = (
        await db.execute(select(table).where(table.model_id == model_id, table.name == name))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No {kind} {name!r} in this model."
        )
    return obj


@router.delete(
    "/workspaces/{ws}/semantic/models/{slug}/datasets/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_dataset(
    slug: str,
    name: str,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a dataset, refusing while anything still binds it.

    The foreign keys are ``ON DELETE CASCADE``, so letting this through would
    take every dimension and metric on the dataset with it — a click that
    silently destroys definitions other people rely on. Naming the dependents
    and refusing costs the caller one extra step and makes the blast radius
    something they chose rather than discovered.
    """
    model = await _editable(db, ctx, slug)
    dataset = await _child(db, model.id, SemanticDataset, name, "dataset")

    dependents: list[str] = []
    for table, kind in (
        (SemanticDimension, "dimension"),
        (SemanticMetric, "metric"),
    ):
        rows = (
            await db.execute(select(table.name).where(table.dataset_id == dataset.id))
        ).scalars()
        dependents += [f"{kind} {row!r}" for row in rows]
    rows = (
        await db.execute(
            select(SemanticRelationship.name).where(
                (SemanticRelationship.left_dataset_id == dataset.id)
                | (SemanticRelationship.right_dataset_id == dataset.id)
            )
        )
    ).scalars()
    dependents += [f"relationship {row!r}" for row in rows]

    if dependents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dataset_in_use",
                "detail": (
                    f"{name!r} still has {', '.join(dependents)}. "
                    "Remove them first — deleting the dataset would delete them too."
                ),
                "dependents": dependents,
            },
        )

    await db.delete(dataset)
    await db.commit()


@router.delete(
    "/workspaces/{ws}/semantic/models/{slug}/dimensions/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_dimension(
    slug: str,
    name: str,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a dimension; metrics measured on it survive, visibly incomplete.

    A metric that loses its time axis must not be deleted along with it — the
    whole point of binding the axis is that a metric without one is a hazard you
    can see, rather than a time filter quietly landing on the wrong column. So
    the binding is cleared and the metric is marked ``unchecked``, which is what
    surfaces it in the next validation report.
    """
    model = await _editable(db, ctx, slug)
    dimension = await _child(db, model.id, SemanticDimension, name, "dimension")

    measured_on = (
        (
            await db.execute(
                select(SemanticMetric).where(SemanticMetric.time_dimension_id == dimension.id)
            )
        )
        .scalars()
        .all()
    )
    for metric in measured_on:
        # Explicit rather than leaning on ON DELETE SET NULL: the intent is part
        # of the behaviour, and SQLite does not enforce the clause by default.
        metric.time_dimension_id = None
        metric.validation_state = "unchecked"
        metric.validation_detail = None
        metric.updated_at = datetime.now(UTC)

    await db.delete(dimension)
    await db.commit()


@router.delete(
    "/workspaces/{ws}/semantic/models/{slug}/metrics/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_metric(
    slug: str,
    name: str,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a metric. Nothing else in a model is defined in terms of one."""
    model = await _editable(db, ctx, slug)
    metric = await _child(db, model.id, SemanticMetric, name, "metric")
    await db.delete(metric)
    await db.commit()


@router.delete(
    "/workspaces/{ws}/semantic/models/{slug}/relationships/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_relationship(
    slug: str,
    name: str,
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a relationship.

    Nothing is deleted with it, but dimensions that were only reachable through
    this join stop being legal for metrics on the other side — which the compiler
    reports as an unreachable dimension at the next query, not silently.
    """
    model = await _editable(db, ctx, slug)
    relationship = await _child(db, model.id, SemanticRelationship, name, "relationship")
    await db.delete(relationship)
    await db.commit()


@router.get("/workspaces/{ws}/semantic/models/{slug}/metrics/{metric_name}/dimensions")
async def metric_dimensions(
    slug: str,
    metric_name: str,
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Which dimensions this metric can legally be sliced by.

    Turns the assistant's dimension choice into a lookup rather than a guess, and
    is what the UI's dimension picker offers.
    """
    try:
        row = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=slug,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(slug) from exc
    loaded = await load_model(db, row, include_unpublished=True)
    try:
        return legal_dimensions(loaded, metric_name)
    except SemanticError as exc:
        raise _semantic_error(exc) from exc


# ── Search and compile ────────────────────────────────────────────────────────


@router.get("/workspaces/{ws}/semantic/search", response_model=SemanticSearchOut)
async def semantic_search(
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
    published_only: bool = Query(default=True),
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> SemanticSearchOut:
    rows = await store.list_models(
        db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        catalogs=ctx.catalogs,
        published_only=published_only,
    )
    loaded = [await load_model(db, row, include_unpublished=not published_only) for row in rows]
    hits = search(loaded, q, limit=limit)
    return SemanticSearchOut(
        hits=[h.as_dict() for h in hits],
        ambiguous=[h.as_dict() for h in ambiguous(hits)],
        broken=search_broken(loaded, q),
    )


@router.post("/workspaces/{ws}/semantic/compile", response_model=CompiledQueryOut)
async def compile_query(
    body: MetricQueryIn,
    published_only: bool = Query(default=True),
    ctx: _Context = Depends(_context("reader")),
    db: AsyncSession = Depends(get_db),
) -> CompiledQueryOut:
    """Compile a semantic request into SQL. Does not execute it.

    Returning SQL rather than results is what keeps execution on one path: the
    caller submits it through ``POST /queries`` like any other statement, and it
    meets the same allowlist and the same grant check on the way.
    """
    try:
        row = await store.get_model(
            db,
            workspace_id=ctx.workspace.id,
            slug=body.model,
            user_id=ctx.user.id,
            catalogs=ctx.catalogs,
            published_only=published_only,
        )
    except store.SemanticNotFound as exc:
        raise _not_found(body.model) from exc

    loaded = await load_model(db, row, include_unpublished=not published_only)

    window = None
    if body.time_range is not None:
        window = TimeRange(
            kind=body.time_range.kind,
            grain=body.time_range.grain,
            n=body.time_range.n,
            start=body.time_range.start,
            end=body.time_range.end,
        )

    query = MetricQuery(
        metrics=tuple(body.metrics),
        dimensions=tuple(body.dimensions),
        grain=body.grain,
        time_range=window,
        filters=tuple(
            DimensionFilter(dimension=f.dimension, op=f.op, values=tuple(f.values))
            for f in body.filters
        ),
        order_by=tuple(OrderTerm(field=o.field, descending=o.descending) for o in body.order_by),
        limit=body.limit,
    )

    try:
        compiled = compile_metric_query(loaded, query)
    except SemanticError as exc:
        raise _semantic_error(exc) from exc

    return CompiledQueryOut(
        sql=compiled.sql,
        definitions_used=compiled.definitions_used,
        warnings=compiled.warnings,
    )


# ── Import ────────────────────────────────────────────────────────────────────


_RECONCILE_MODES = ("none", "provider_run")


@router.post("/workspaces/{ws}/semantic/imports/{provider}", response_model=SemanticImportOut)
async def import_semantics(
    provider: str,
    request: Request,
    reconcile: str = Query(default="provider_run"),
    ctx: _Context = Depends(_context("writer")),
    db: AsyncSession = Depends(get_db),
) -> SemanticImportOut:
    """Publish semantic definitions from an external producer.

    Imported models arrive as **drafts**. An import is a publishing act by a
    pipeline, not by a person, and the whole value of the published gate is that
    somebody decided — so promoting them stays a separate, deliberate step.

    ``reconcile=provider_run`` (the default) treats the payload as this provider's
    complete set and retires models it no longer declares. Pass ``none`` when
    publishing a subset, or the models left out will be removed.
    """
    if reconcile not in _RECONCILE_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"reconcile must be one of: {', '.join(_RECONCILE_MODES)}",
        )
    if provider == NATIVE_PROVIDER:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{NATIVE_PROVIDER!r} is reserved for definitions authored in DuckHaven and "
                "cannot be imported. Use a provider name of your own."
            ),
        )
    try:
        adapter = get_adapter(provider)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No semantic adapter for provider {provider!r}.",
        ) from exc

    # Read the artifact as raw bytes rather than a typed body. One route serves
    # two formats — a hand-written YAML document and a machine-written JSON
    # manifest — and this is what lets both arrive byte-for-byte as published,
    # whatever content type the publisher sent. It also keeps a YAML parse error
    # pointing at the line the author wrote rather than one FastAPI re-encoded.
    # (The lineage import route takes a typed `dict` body because every producer
    # it accepts publishes JSON.)
    raw = (await request.body()).decode("utf-8", errors="replace")

    resolver = Resolver(ctx.catalogs)
    try:
        parsed = await adapter(raw, resolve=resolver)
    except SemanticDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # Binding a dataset names a table, so an import needs the same tier on every
    # table it touches that reading that table's metadata needs. Checked before
    # anything is written, so a partially-authorized payload writes nothing.
    by_id = {c.id: c for c in ctx.catalogs}
    for model in parsed.models:
        for dataset in model.datasets:
            catalog = by_id.get(dataset.catalog_id)
            if catalog is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            await grant_service.enforce_leaf(
                db,
                ctx.workspace.id,
                catalog,
                ctx.user.id,
                schema=dataset.schema_name,
                table=dataset.table_name,
                need="metadata",
            )

    # The producer's own batch id where it publishes one, so an import can be
    # traced back to the run that produced it — the same rule, and the same
    # helper name, as the lineage route. Only a producer with no invocation of
    # its own (a hand-written YAML document) falls back to a generated id.
    run_id = None
    if provider == "dbt":
        from api.services.semantic.providers.dbt import run_id as dbt_run_id

        with contextlib.suppress(Exception):
            run_id = dbt_run_id(json.loads(raw))
    run_id = run_id or uuid.uuid4().hex

    result = await upsert_models(
        db,
        parsed.models,
        provider=provider,
        provider_run_id=run_id,
        workspace_id=ctx.workspace.id,
        owner_id=ctx.user.id,
    )
    if reconcile == "provider_run":
        result.removed = await reconcile_provider_run(
            db,
            provider=provider,
            workspace_id=ctx.workspace.id,
            model_slugs=parsed.model_slugs,
        )
    await db.commit()

    skipped = [
        {"ref": s.ref, "reason": s.reason} if hasattr(s, "ref") else s
        for s in [*parsed.skipped, *result.skipped]
    ]
    return SemanticImportOut(
        provider=provider,
        run_id=run_id,
        created=result.created,
        updated=result.updated,
        removed=result.removed,
        skipped=skipped,
    )


@router.delete("/workspaces/{ws}/semantic/imports", status_code=status.HTTP_204_NO_CONTENT)
async def purge_import(
    provider: str = Query(...),
    ctx: _Context = Depends(_context("owner")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove everything one provider published into this workspace."""
    try:
        await purge_provider(db, provider=provider, workspace_id=ctx.workspace.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await db.commit()


# ── Impact ────────────────────────────────────────────────────────────────────


async def table_semantics(
    ws: str,
    schema: str,
    table: str,
    catalog: str | None = None,
    column: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TableSemanticsOut:
    """Which semantic definitions depend on one physical table.

    The direction lineage cannot answer: lineage knows what feeds this table, not
    which published business definitions would break if a column went away.
    """
    from api.services.workspace import get_default_catalog

    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="reader")

    resolved = (
        await resolve_catalog(db, workspace.id, catalog)
        if catalog is not None
        else await get_default_catalog(db, workspace.id)
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace has no catalogs attached."
        )
    await grant_service.enforce_leaf(
        db, workspace.id, resolved, user.id, schema=schema, table=table, need="metadata"
    )

    found = await dependents_for_table(
        db,
        workspace_id=workspace.id,
        catalog_id=resolved.id,
        schema_name=schema,
        table_name=table,
        column=column,
    )
    return TableSemanticsOut(dependents=[DependentOut(**d.as_dict()) for d in found])


# Registered under both bases, matching the lineage read route: the canonical
# per-catalog path and the legacy shim that resolves the workspace default.
_CANON = "/workspaces/{ws}/catalogs/{catalog}/schemas"
_LEGACY = "/workspaces/{ws}/schemas"

for _base in (_CANON, _LEGACY):
    router.add_api_route(
        f"{_base}/{{schema}}/tables/{{table}}/semantic",
        table_semantics,
        methods=["GET"],
        response_model=TableSemanticsOut,
        tags=["semantic"],
    )
