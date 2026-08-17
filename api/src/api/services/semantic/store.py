"""Reading and writing semantic definitions, and deciding who may see them.

The visibility rule here is stricter than lineage's, and the difference is
deliberate rather than an oversight. Lineage *redacts* a node the caller cannot
see: the graph keeps its shape while the name is withheld, which is safe because
lineage exposes structure. A metric exposes a **value**. A model whose bindings a
caller cannot read would let them compute a number over data they have no access
to, which is not a redaction problem — it is the access-control failure itself.

So a model is all-or-nothing per caller: if any table it binds is invisible, the
whole model is invisible, and it is absent rather than forbidden — matching the
house 404-at-the-leaf convention where denied and nonexistent look the same.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.models.semantic import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)
from api.services import grants as grant_service


class SemanticNotFound(Exception):
    """No such model, or none this caller may see. Deliberately the same thing."""


async def _catalogs_by_id(db: AsyncSession, catalogs: list[Catalog]) -> dict[uuid.UUID, Catalog]:
    return {c.id: c for c in catalogs}


async def visible(
    db: AsyncSession,
    model: SemanticModel,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    catalogs: list[Catalog],
) -> bool:
    """Whether this caller may see the whole of a model.

    Every bound table is checked at the ``metadata`` tier — the same tier
    ``describe_table`` needs — because a metric names its table and its columns,
    so seeing the definition is seeing that much of the schema.
    """
    by_id = await _catalogs_by_id(db, catalogs)
    datasets = list(
        (
            await db.execute(select(SemanticDataset).where(SemanticDataset.model_id == model.id))
        ).scalars()
    )
    for ds in datasets:
        catalog = by_id.get(ds.catalog_id)
        if catalog is None:
            # Bound to a catalog this workspace no longer attaches. Not a
            # permission failure, but not readable either.
            return False
        try:
            await grant_service.enforce_leaf(
                db,
                workspace_id,
                catalog,
                user_id,
                schema=ds.schema_name,
                table=ds.table_name,
                need="metadata",
            )
        except HTTPException:
            # `enforce_leaf` raises 404 for a denied leaf. Deliberately narrow:
            # a database error here must surface as a database error, not be
            # laundered into "you cannot see this model".
            return False
    return True


async def list_models(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    catalogs: list[Catalog],
    status: str | None = None,
    published_only: bool = False,
) -> list[SemanticModel]:
    """Models in this workspace this caller may see, newest name order."""
    stmt = select(SemanticModel).where(SemanticModel.workspace_id == workspace_id)
    if published_only:
        stmt = stmt.where(SemanticModel.status == "published")
    elif status:
        stmt = stmt.where(SemanticModel.status == status)
    rows = list((await db.execute(stmt.order_by(SemanticModel.slug))).scalars())

    allowed: list[SemanticModel] = []
    for row in rows:
        if await visible(db, row, workspace_id=workspace_id, user_id=user_id, catalogs=catalogs):
            allowed.append(row)
    return allowed


async def get_model(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    slug: str,
    user_id: uuid.UUID,
    catalogs: list[Catalog],
    published_only: bool = False,
) -> SemanticModel:
    """One model, or :class:`SemanticNotFound`."""
    row = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.workspace_id == workspace_id,
                SemanticModel.slug == slug,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise SemanticNotFound(slug)
    if published_only and row.status != "published":
        raise SemanticNotFound(slug)
    if not await visible(db, row, workspace_id=workspace_id, user_id=user_id, catalogs=catalogs):
        raise SemanticNotFound(slug)
    return row


async def children(db: AsyncSession, model_id: uuid.UUID):
    """Every child collection of one model, in one place."""
    datasets = list(
        (
            await db.execute(select(SemanticDataset).where(SemanticDataset.model_id == model_id))
        ).scalars()
    )
    dimensions = list(
        (
            await db.execute(
                select(SemanticDimension).where(SemanticDimension.model_id == model_id)
            )
        ).scalars()
    )
    metrics = list(
        (
            await db.execute(select(SemanticMetric).where(SemanticMetric.model_id == model_id))
        ).scalars()
    )
    relationships = list(
        (
            await db.execute(
                select(SemanticRelationship).where(SemanticRelationship.model_id == model_id)
            )
        ).scalars()
    )
    return datasets, dimensions, metrics, relationships


async def catalog_map(db: AsyncSession, catalogs: list[Catalog]) -> dict[uuid.UUID, Catalog]:
    return {c.id: c for c in catalogs}
