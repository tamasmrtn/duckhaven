"""Telling a renamed table from a new one wearing its name.

Lineage keys on a table's **address** — ``(catalog, schema, table)`` — because
that is what makes traversal a single indexed equality lookup and what lets the
graph exist without the control plane caching catalog structure (I3). The cost of
that choice is that a rename moves the address, and the edges do not follow: the
renamed table appears to have no history and the old name's edges hang until
something deletes them.

Iceberg gives every table an id that survives a rename, which is precisely the
missing piece — and it distinguishes the two cases that look identical from the
outside but demand opposite handling:

============================  ==================  ==================
                              address             Iceberg id
============================  ==================  ==================
renamed                       changed             same
dropped, recreated same name  same                changed
============================  ==================  ==================

Getting that backwards is the worst outcome available here: carrying a dropped
table's lineage onto an unrelated new table with the same name would have the
graph confidently assert relationships that never existed.

**No lookup is added anywhere.** The id is recorded only where a handler already
holds the table's Iceberg metadata — reading a table's detail, creating one —
and reconciliation is a single indexed query against ``table_metadata``, a row
those handlers are reading regardless. Nothing here runs when a lineage graph is
requested; identity is settled when a table is observed, not when its lineage is
read.

The consequence worth being honest about: DuckHaven has no rename API, so a
rename arrives out of band, and the graph is repaired the next time the table is
looked at rather than the moment it moves. That is a self-healing gap rather than
a permanent loss, which is what today's behaviour is.
"""

from __future__ import annotations

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.table_metadata import TableMetadata
from api.services.lineage.ingest import delete_table_lineage, rekey_table_lineage

logger = logging.getLogger(__name__)


async def reconcile_table_identity(
    db: AsyncSession,
    *,
    catalog_id: uuid.UUID,
    schema: str,
    table: str,
    table_uuid: str | None,
) -> str | None:
    """Record a table's Iceberg id, repairing lineage if the id has moved.

    Returns what it concluded — ``"unchanged"``, ``"recorded"``, ``"renamed"``,
    ``"recreated"`` — or ``None`` when there was no id to work with. Writes
    nothing in the ``"unchanged"`` case, which is every call but the first after
    a change.

    The caller is responsible for committing; this participates in whatever
    transaction it is handed so a repair and the read that noticed it either both
    land or neither does.
    """
    if not table_uuid:
        # Polaris reported no id: an older metadata version, or a table type that
        # does not carry one. Nothing to conclude, and guessing from the name
        # alone is exactly what this module exists to avoid.
        return None

    here = (
        await db.execute(
            sa.select(TableMetadata).where(
                TableMetadata.catalog_id == catalog_id,
                TableMetadata.schema_name == schema,
                TableMetadata.table_name == table,
            )
        )
    ).scalar_one_or_none()

    if here is not None and here.table_uuid == table_uuid:
        return "unchanged"

    if here is not None and here.table_uuid is not None:
        # Same address, different table. Whatever the old lineage described is
        # gone; the new table has to earn its own. Deliberately the same rule the
        # drop path applies, because this *is* a drop that DuckHaven did not see.
        await delete_table_lineage(db, catalog_id, schema, table)
        here.table_uuid = table_uuid
        logger.info(
            "Table %s.%s in catalog %s was recreated; its previous lineage was removed",
            schema,
            table,
            catalog_id,
        )
        return "recreated"

    # The id is either new to us or known under another name. Only the second is
    # a rename, and the Iceberg id is what says so — a table id is unique to a
    # table, so finding it at a second address in the same catalog means the
    # address moved.
    elsewhere = (
        (
            await db.execute(
                sa.select(TableMetadata).where(
                    TableMetadata.catalog_id == catalog_id,
                    TableMetadata.table_uuid == table_uuid,
                    sa.not_(
                        sa.and_(
                            TableMetadata.schema_name == schema,
                            TableMetadata.table_name == table,
                        )
                    ),
                )
            )
        )
        .scalars()
        .first()
    )

    if elsewhere is None:
        if here is not None:
            here.table_uuid = table_uuid
            return "recorded"
        # No sidecar row yet — a table DuckHaven has never written to or created.
        # One is added so the identity is remembered for next time; the other
        # columns stay empty, which is what they are for a table nobody has
        # touched through DuckHaven.
        db.add(
            TableMetadata(
                catalog_id=catalog_id,
                schema_name=schema,
                table_name=table,
                table_uuid=table_uuid,
            )
        )
        await db.flush()
        return "recorded"

    old_schema, old_table = elsewhere.schema_name, elsewhere.table_name
    await rekey_table_lineage(
        db,
        catalog_id,
        old_schema=old_schema,
        old_table=old_table,
        new_schema=schema,
        new_table=table,
    )

    if here is None:
        # The sidecar can simply follow the table, keeping its ownership and
        # write history — which a rename does not change either.
        elsewhere.schema_name = schema
        elsewhere.table_name = table
    else:
        # Something already occupies the new address, so the old row cannot move
        # onto it. The identity is what matters; the rest of the new row's facts
        # are about the table as DuckHaven has seen it there.
        here.table_uuid = table_uuid
        await db.delete(elsewhere)
    await db.flush()

    logger.info(
        "Table %s.%s in catalog %s was renamed to %s.%s; its lineage moved with it",
        old_schema,
        old_table,
        catalog_id,
        schema,
        table,
    )
    return "renamed"
