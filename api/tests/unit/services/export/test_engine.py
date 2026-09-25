"""Exporting a DuckLake catalog to Iceberg.

The decisions around the COPY: what is created, dispatched, and deliberately
*not* cleaned up on failure.
"""

from __future__ import annotations

import uuid

import pytest

from api.models.query import Query
from api.services.export import STATUS_COMPLETED, STATUS_COPYING, STATUS_FAILED
from api.services.export import engine as export_engine


class _Export:
    """A CatalogExport stand-in; the engine touches only these fields."""

    def __init__(self, **over):
        self.id = uuid.uuid4()
        self.source_catalog_id = uuid.uuid4()
        self.target_catalog_id = uuid.uuid4()
        self.target_name = "berg"
        self.target_storage_backend_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        self.status = STATUS_COPYING
        self.query_id = None
        self.tables_total = 3
        self.tables_done = 0
        self.cancel_requested = False
        self.error = None
        self.created_by = uuid.uuid4()
        self.started_at = None
        self.finished_at = None
        self.__dict__.update(over)


class _Db:
    """Just enough session: get() by type, and commits that do nothing."""

    def __init__(self, objects: dict):
        self._objects = objects

    async def get(self, model, ident):  # noqa: ANN001
        return self._objects.get(model)

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_a_completed_query_completes_the_export():
    export = _Export(query_id=uuid.uuid4())
    db = _Db({Query: Query(workspace_id=uuid.uuid4(), sql="x", status="done")})

    await export_engine._advance(db, export)

    assert export.status == STATUS_COMPLETED
    assert export.tables_done == export.tables_total
    assert export.error is None


@pytest.mark.asyncio
async def test_a_failed_query_leaves_the_target_rather_than_dropping_it():
    """It may be partially populated, and silently deleting data a user can
    already see in the UI is worse than leaving something they can inspect."""
    export = _Export(query_id=uuid.uuid4())
    db = _Db(
        {
            Query: Query(workspace_id=uuid.uuid4(), sql="x", status="failed", error="disk full"),
        }
    )

    await export_engine._advance(db, export)

    assert export.status == STATUS_FAILED
    assert "disk full" in export.error
    assert "drop it before retrying" in export.error
    # Never cleared: the catalog exists and the operator decides.
    assert export.target_catalog_id is not None


@pytest.mark.asyncio
async def test_a_running_query_leaves_the_export_alone():
    """The next tick looks again; settling early would report a copy finished
    while it is still writing."""
    export = _Export(query_id=uuid.uuid4())
    db = _Db({Query: Query(workspace_id=uuid.uuid4(), sql="x", status="running")})

    await export_engine._advance(db, export)

    assert export.status == STATUS_COPYING
    assert export.finished_at is None


@pytest.mark.asyncio
async def test_a_missing_query_record_fails_rather_than_hanging():
    export = _Export(query_id=uuid.uuid4())
    await export_engine._advance(_Db({}), export)

    assert export.status == STATUS_FAILED


@pytest.mark.asyncio
async def test_cancelling_before_dispatch_never_dispatches():
    export = _Export(status="pending", cancel_requested=True)

    await export_engine.process_export(_Db({}), object(), export)

    assert export.status == STATUS_FAILED
    assert export.query_id is None


def test_the_copy_statement_quotes_both_slugs():
    """One statement does the whole catalog. Slugs are identifiers here, not
    values, so they are quoted as identifiers."""
    assert export_engine.copy_statement("lake", "berg") == 'COPY FROM DATABASE "lake" TO "berg"'


def test_the_copy_statement_is_a_deep_copy_of_current_state():
    """It carries no snapshot clause, because there is nothing to carry: the
    statement copies the latest state and not the history. Stated in the
    confirm dialog for the same reason it is asserted here."""
    statement = export_engine.copy_statement("lake", "berg")
    assert "AT (" not in statement
    assert "VERSION" not in statement
