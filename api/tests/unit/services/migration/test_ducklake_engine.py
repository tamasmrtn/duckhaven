"""The DuckLake arm of the migration phase machine.

Storage IO is stubbed; these assert the phase decisions and that cutover is
re-entrant after a crash.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from api.services.migration import STATUS_PENDING
from api.services.migration import ducklake as dl


async def _noop_log(db, migration_id, level, message):  # noqa: ANN001
    return None


class _Migration:
    """A CatalogMigration stand-in; these phases touch only these fields."""

    def __init__(self, **over):
        self.id = uuid.uuid4()
        self.catalog_id = uuid.uuid4()
        self.source_storage_backend_id = uuid.uuid4()
        self.target_storage_backend_id = uuid.uuid4()
        self.status = STATUS_PENDING
        self.started_at = None
        self.source_data_path = None
        self.target_data_path = None
        self.tables_total = 0
        self.tables_done = 0
        self.bytes_copied = 0
        self.cancel_requested = False
        self.__dict__.update(over)


def test_the_table_prefix_is_the_schema_and_table_paths_joined():
    """A file's location nests under both, so copying only the data path's own
    objects would move nothing that matters."""
    assert dl.parsed_prefix("s3://bucket/lake/raw/") == "lake/raw/"


def test_copy_skips_an_object_already_the_right_size_at_the_target(monkeypatch):
    """Copy-if-absent, so a crashed migration resumes without re-moving what it
    already moved."""
    copied: list[str] = []
    monkeypatch.setattr(
        dl,
        "list_objects",
        lambda ctx, prefix: [("s3://a/x/1.parquet", 10), ("s3://a/x/2.parquet", 20)],
    )
    monkeypatch.setattr(
        "api.services.migration.storage_io.object_size",
        lambda ctx, uri: 10 if uri.endswith("1.parquet") else None,
    )

    def _copy(src, dst, src_uri, dst_uri):
        copied.append(dst_uri)
        return 20

    monkeypatch.setattr(dl, "copy_object", _copy)

    moved = dl._copy_prefix(object(), object(), "s3://a/x/", "s3://b/x/")

    assert copied == ["s3://b/x/2.parquet"]
    assert moved == 20


def test_verify_reports_the_first_object_that_did_not_arrive(monkeypatch):
    """Size-for-size, not just presence: a truncated copy is worse than a
    missing one because nothing later would notice."""
    src_objs = [("s3://a/p/1", 10), ("s3://a/p/2", 20)]
    dst_objs = [("s3://b/p/1", 10), ("s3://b/p/2", 19)]

    def _list(ctx, prefix):
        return src_objs if prefix.startswith("s3://a") else dst_objs

    monkeypatch.setattr(dl, "list_objects", _list)

    missing = dl._missing_at_target(object(), object(), "s3://a/p/", "s3://b/p/")

    assert missing == ["s3://a/p/2"]


def test_verify_passes_when_every_object_matches(monkeypatch):
    objs = [("s3://a/p/1", 10)]
    monkeypatch.setattr(
        dl,
        "list_objects",
        lambda ctx, prefix: objs if prefix.startswith("s3://a") else [("s3://b/p/1", 10)],
    )
    assert dl._missing_at_target(object(), object(), "s3://a/p/", "s3://b/p/") == []


@pytest.mark.asyncio
async def test_provision_waits_while_statements_are_still_running(monkeypatch, db_session):
    """The submit-time freeze cannot catch a statement that was already running
    when the migration row committed, and on DuckLake that write lands under the
    source prefix after the copy started."""
    migration = _Migration(started_at=datetime.now(tz=UTC))
    monkeypatch.setattr(dl, "in_flight_statements", lambda db, cid: _async(3))
    monkeypatch.setattr(dl, "absolute_path_count", lambda cat: _async(0))

    called: list[str] = []
    monkeypatch.setattr(dl, "set_agent_dml", lambda cat, allowed: called.append("revoked"))

    # A catalog that resolves; the phase returns before touching storage.
    async def _get(model, ident):  # noqa: ANN001
        return _FakeCatalog()

    db_session.get = _get  # type: ignore[method-assign]

    await dl.provision(db_session, migration, _noop_log)

    assert migration.status == STATUS_PENDING  # still waiting
    assert called == []  # writes not revoked until it is quiet


@pytest.mark.asyncio
async def test_provision_fails_once_the_quiesce_window_has_elapsed(monkeypatch, db_session):
    """Waiting forever would leave a migration pending and a catalog half-frozen."""
    migration = _Migration(
        started_at=datetime.now(tz=UTC) - timedelta(seconds=dl.QUIESCE_TIMEOUT_S + 60)
    )
    monkeypatch.setattr(dl, "in_flight_statements", lambda db, cid: _async(1))
    monkeypatch.setattr(dl, "absolute_path_count", lambda cat: _async(0))

    async def _get(model, ident):  # noqa: ANN001
        return _FakeCatalog()

    db_session.get = _get  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="never went quiet"):
        await dl.provision(db_session, migration, _noop_log)


@pytest.mark.asyncio
async def test_provision_refuses_when_files_are_recorded_with_absolute_paths(
    monkeypatch, db_session
):
    """They do not move with the data path, so the copy would silently leave
    them pointing at the old location."""
    migration = _Migration()
    monkeypatch.setattr(dl, "absolute_path_count", lambda cat: _async(4))

    async def _get(model, ident):  # noqa: ANN001
        return _FakeCatalog()

    db_session.get = _get  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="absolute path"):
        await dl.provision(db_session, migration, _noop_log)


class _FakeCatalog:
    slug = "lake"
    kind = "ducklake"
    metadata_schema = "cat_lake"
    id = uuid.uuid4()
    storage_backend = None


def _async(value):
    async def _inner():
        return value

    return _inner()


def test_no_phase_reads_the_catalogs_lazy_storage_backend():
    """The lazy relationship raises MissingGreenlet under asyncpg.

    Asserted against the source because SQLite resolves the lazy load happily,
    so a behavioural test would pass with the bug present.
    """
    import inspect

    source = inspect.getsource(dl)
    offenders = [
        line.strip()
        for line in source.splitlines()
        # The cutover assigns the *column*, which is not a relationship load.
        if "catalog.storage_backend" in line
        and "catalog.storage_backend_id" not in line
        and not line.strip().startswith("#")
    ]
    assert offenders == [], offenders
