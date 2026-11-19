"""info_schema against Postgres: who sees which rows, and how they are kept current.

The row filters are Postgres row-level security, so they are proved here, as the
reader login connects with the settings an agent sends.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.catalog_backends import (
    CatalogBackendNotFound,
    CatalogColumnInfo,
    CatalogDescription,
    CatalogRoutineInfo,
    CatalogSchemaInfo,
    CatalogTableInfo,
)
from api.services.system_catalog import metadata, publisher, store

pytestmark = pytest.mark.integration

INFO = ",".join(t.qualified for t in store.TABLES if t.schema == store.INFO)


async def _reader(factory, *, readable: str) -> asyncpg.Connection:
    """The reader login, connected with only the tables a query may read set."""
    async with factory() as db:
        password = await store.reader_password(db)
    parts = urlsplit(settings.system_catalog_database_url)
    netloc = f"{store.READER_ROLE}:{password}@{parts.hostname}:{parts.port or 5432}"
    return await asyncpg.connect(
        urlunsplit(("postgresql", netloc, parts.path, "", "")),
        server_settings={"duckhaven.readable": readable},
    )


async def _seen(factory, workspace: uuid.UUID | None, *principals: uuid.UUID) -> dict:
    """What a query in ``workspace`` run for ``principals`` reads of info_schema."""
    reader = await _reader(factory, readable=INFO)
    try:
        if workspace is not None:
            await reader.execute(f"SET duckhaven.workspace = '{workspace}'")
        await reader.execute(f"SET duckhaven.principals = '{','.join(map(str, principals))}'")
        return {
            "catalogs": {
                r[0] for r in await reader.fetch("SELECT catalog_name FROM info_schema.catalogs")
            },
            "schemata": {
                (r[0], r[1])
                for r in await reader.fetch(
                    "SELECT catalog_name, schema_name FROM info_schema.schemata"
                )
            },
            "tables": {
                (r[0], r[1], r[2])
                for r in await reader.fetch(
                    "SELECT catalog_name, schema_name, table_name FROM info_schema.tables"
                )
            },
            "columns": {
                (r[0], r[1], r[2])
                for r in await reader.fetch(
                    "SELECT DISTINCT catalog_name, schema_name, table_name FROM info_schema.columns"
                )
            },
        }
    finally:
        await reader.close()


async def _read(factory, sql: str, workspace: uuid.UUID, *principals: uuid.UUID) -> set:
    """The rows ``sql`` reads of info_schema in ``workspace``, run for ``principals``."""
    reader = await _reader(factory, readable=INFO)
    try:
        await reader.execute(f"SET duckhaven.workspace = '{workspace}'")
        await reader.execute(f"SET duckhaven.principals = '{','.join(map(str, principals))}'")
        return {tuple(r) for r in await reader.fetch(sql)}
    finally:
        await reader.close()


# ── Who sees what ─────────────────────────────────────────────────────────────

WS, ELSEWHERE = uuid.uuid4(), uuid.uuid4()
OPEN, SCOPED = uuid.uuid4(), uuid.uuid4()
ANA, BOB, CY, ASSISTANT = (uuid.uuid4() for _ in range(4))
TABLES = [
    (OPEN, "open_cat", "raw", "events"),
    (SCOPED, "scoped_cat", "analytics", "orders"),
    (SCOPED, "scoped_cat", "analytics", "customers"),
    (SCOPED, "scoped_cat", "finance", "ledger"),
    # Names a careless key would confuse: `a` + `b.c` and `a.b` + `c`.
    (SCOPED, "scoped_cat", "a", "b.c"),
    (SCOPED, "scoped_cat", "a.b", "c"),
]
ROUTINES = [
    (OPEN, "open_cat", "raw", "clean"),
    (SCOPED, "scoped_cat", "analytics", "recent"),
    (SCOPED, "scoped_cat", "finance", "fx"),
]
# Each semantic model, its workspace, and the tables its datasets are on.
MODELS = {
    "orders": (WS, [(SCOPED, "analytics", "orders"), (OPEN, "raw", "events")]),
    "books": (WS, [(SCOPED, "analytics", "orders"), (SCOPED, "finance", "ledger")]),
    "empty": (WS, []),
    "detached": (WS, [(uuid.uuid4(), "raw", "events")]),
    "elsewhere": (ELSEWHERE, [(OPEN, "raw", "events")]),
}
MODEL_IDS = {slug: uuid.uuid4() for slug in MODELS}


@pytest.fixture
async def seeded(system_store):
    """Two catalogs in one workspace, one open and one scoped, and these grants on
    the scoped one: Ana `orders` and `a`.`b.c`, Bob all of it, Cy all of it but no
    membership, and the assistant schema `analytics`."""
    assert await store.ensure(system_store)
    now = datetime.now(UTC)
    rows = {
        store.ACL_ATTACHMENTS: [
            {"workspace_id": WS, "catalog_id": OPEN, "access_mode": "open"},
            {"workspace_id": WS, "catalog_id": SCOPED, "access_mode": "scoped"},
        ],
        store.ACL_MEMBERS: [{"workspace_id": WS, "user_id": u} for u in (ANA, BOB, ASSISTANT)],
        store.ACL_SCOPES: [
            {
                "user_id": ANA,
                "catalog_id": SCOPED,
                "schema_name": "analytics",
                "table_name": "orders",
            },
            {"user_id": ANA, "catalog_id": SCOPED, "schema_name": "a", "table_name": "b.c"},
            {"user_id": BOB, "catalog_id": SCOPED, "schema_name": None, "table_name": None},
            {"user_id": CY, "catalog_id": SCOPED, "schema_name": None, "table_name": None},
            {
                "user_id": ASSISTANT,
                "catalog_id": SCOPED,
                "schema_name": "analytics",
                "table_name": None,
            },
        ],
        store.INFO_CATALOGS: [
            {
                "workspace_id": WS,
                "catalog_id": cat,
                "catalog_name": name,
                "catalog_kind": "iceberg_polaris",
                "access_mode": mode,
                "is_default": mode == "open",
            }
            for cat, name, mode in ((OPEN, "open_cat", "open"), (SCOPED, "scoped_cat", "scoped"))
        ],
        store.INFO_SCHEMATA: [
            {"catalog_id": c, "catalog_name": n, "schema_name": s, "refreshed_at": now}
            for c, n, s in {(c, n, s) for c, n, s, _ in TABLES}
        ],
        store.INFO_TABLES: [
            {
                "catalog_id": c,
                "catalog_name": n,
                "schema_name": s,
                "table_name": t,
                "table_type": "BASE TABLE",
                "table_format": "ICEBERG",
                "column_count": 1,
                "refreshed_at": now,
            }
            for c, n, s, t in TABLES
        ],
        store.INFO_COLUMNS: [
            {
                "catalog_id": c,
                "catalog_name": n,
                "schema_name": s,
                "table_name": t,
                "column_name": "id",
                "ordinal_position": 1,
                "data_type": "BIGINT",
                "source_type": "long",
                "is_nullable": "NO",
            }
            for c, n, s, t in TABLES
        ],
        store.INFO_ROUTINES: [
            {
                "catalog_id": c,
                "catalog_name": n,
                "schema_name": s,
                "routine_name": r,
                "overload": 0,
                "routine_type": "macro",
                "parameters": "x",
                "definition": "x",
                "refreshed_at": now,
            }
            for c, n, s, r in ROUTINES
        ],
        store.SEMANTIC_MODELS: [
            {
                "model_id": MODEL_IDS[slug],
                "workspace_id": workspace,
                "model_slug": slug,
                "name": slug,
                "status": "published",
                "provider": "native",
                "refreshed_at": now,
            }
            for slug, (workspace, _) in MODELS.items()
        ],
        # One metric per model: each of its tables is filtered as the model is.
        store.SEMANTIC_METRICS: [
            {
                "metric_id": uuid.uuid4(),
                "model_id": MODEL_IDS[slug],
                "workspace_id": workspace,
                "model_slug": slug,
                "dataset_id": uuid.uuid4(),
                "name": f"{slug}_count",
                "agg": "count",
                "status": "published",
                "validation_state": "valid",
                "refreshed_at": now,
            }
            for slug, (workspace, _) in MODELS.items()
        ],
        store.ACL_SEMANTIC_TABLES: [
            {
                "workspace_id": workspace,
                "model_id": MODEL_IDS[slug],
                "catalog_id": c,
                "schema_name": sc,
                "table_name": t,
            }
            for slug, (workspace, tables) in MODELS.items()
            for c, sc, t in tables
        ],
    }
    async with store.get_engine().begin() as conn:
        for table, values in rows.items():
            await conn.execute(sa.insert(table), values)
    return system_store


async def test_an_open_catalog_shows_everything_and_a_scoped_one_what_a_grant_covers(seeded):
    seen = await _seen(seeded, WS, ANA)

    assert seen["catalogs"] == {"open_cat", "scoped_cat"}
    assert seen["tables"] == {
        ("open_cat", "raw", "events"),
        ("scoped_cat", "analytics", "orders"),
        ("scoped_cat", "a", "b.c"),
    }
    assert seen["columns"] == seen["tables"]
    # A schema shows for a grant on one of its tables.
    assert seen["schemata"] == {
        ("open_cat", "raw"),
        ("scoped_cat", "analytics"),
        ("scoped_cat", "a"),
    }


async def test_a_catalog_grant_shows_the_whole_catalog(seeded):
    seen = await _seen(seeded, WS, BOB)

    assert seen["tables"] == {(n, s, t) for _, n, s, t in TABLES}


async def test_a_grant_without_membership_shows_nothing_scoped(seeded):
    seen = await _seen(seeded, WS, CY)

    assert seen["tables"] == {("open_cat", "raw", "events")}


async def test_a_query_run_for_someone_shows_only_what_both_may_see(seeded):
    """The assistant sees schema `analytics`; Ana one table in it, and one elsewhere."""
    seen = await _seen(seeded, WS, ASSISTANT, ANA)

    assert seen["tables"] == {("open_cat", "raw", "events"), ("scoped_cat", "analytics", "orders")}
    assert seen["schemata"] == {("open_cat", "raw"), ("scoped_cat", "analytics")}


@pytest.mark.parametrize(
    ("workspace", "principals"), [(ELSEWHERE, (BOB,)), (WS, ()), (None, (BOB,))]
)
async def test_without_its_workspace_or_anyone_to_run_for_nothing_shows(
    seeded, workspace, principals
):
    seen = await _seen(seeded, workspace, *principals)

    assert seen == {"catalogs": set(), "schemata": set(), "tables": set(), "columns": set()}


async def test_the_reader_cannot_read_what_the_filters_consult(seeded):
    reader = await _reader(seeded, readable=INFO)
    try:
        for table in (
            "_acl.scopes",
            "_acl.members",
            "_acl.attachments",
            "_acl.semantic_tables",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await reader.fetch(f"SELECT * FROM {table}")
    finally:
        await reader.close()


_ROUTINES = "SELECT catalog_name, schema_name, routine_name FROM info_schema.routines"


async def test_a_macro_shows_for_its_schema_not_for_one_table_in_it(seeded):
    """Ana holds grants on tables only; the assistant on schema `analytics`."""
    assert await _read(seeded, _ROUTINES, WS, ANA) == {("open_cat", "raw", "clean")}
    assert await _read(seeded, _ROUTINES, WS, ASSISTANT) == {
        ("open_cat", "raw", "clean"),
        ("scoped_cat", "analytics", "recent"),
    }
    assert await _read(seeded, _ROUTINES, WS, BOB) == {(n, s, r) for _, n, s, r in ROUTINES}


_MODELS = "SELECT model_slug FROM info_schema.semantic_models"
_METRICS = "SELECT model_slug FROM info_schema.semantic_metrics"


@pytest.mark.parametrize(
    ("principals", "expected"),
    [
        # Bob sees every table of the scoped catalog.
        ((BOB,), {"orders", "books", "empty"}),
        # Ana sees `orders`, not `ledger`.
        ((ANA,), {"orders", "empty"}),
        ((ASSISTANT,), {"orders", "empty"}),
        ((ASSISTANT, ANA), {"orders", "empty"}),
        # Cy is not a member: only the open catalog's tables.
        ((CY,), {"empty"}),
    ],
)
async def test_a_semantic_model_shows_only_when_each_of_its_tables_does(
    seeded, principals, expected
):
    """A model on a catalog the workspace does not attach, or of another workspace,
    shows to no one."""
    assert {r[0] for r in await _read(seeded, _MODELS, WS, *principals)} == expected
    assert {r[0] for r in await _read(seeded, _METRICS, WS, *principals)} == expected


async def test_a_table_left_out_of_the_readable_ones_shows_nothing(seeded):
    reader = await _reader(seeded, readable="info_schema.catalogs")
    try:
        await reader.execute(f"SET duckhaven.workspace = '{WS}'")
        await reader.execute(f"SET duckhaven.principals = '{BOB}'")
        assert await reader.fetchval("SELECT count(*) FROM info_schema.catalogs") == 2
        assert await reader.fetchval("SELECT count(*) FROM info_schema.tables") == 0
    finally:
        await reader.close()


# ── Keeping it current ────────────────────────────────────────────────────────


async def _control_plane(factory) -> tuple[Workspace, User, Catalog, Catalog]:
    """A workspace with an open Iceberg catalog and a scoped DuckLake one."""
    ana = User(id=uuid.uuid4(), email="ana@example.com", name="Ana")
    workspace = Workspace(id=uuid.uuid4(), slug="sales", name="Sales")
    backend = StorageBackend(kind="object_store", name="bundled", root_uri="", created_by=ana.id)
    iceberg = Catalog(
        id=uuid.uuid4(),
        slug="sales",
        name="sales",
        kind="iceberg_polaris",
        polaris_name="sales",
        created_by=ana.id,
    )
    lake = Catalog(
        id=uuid.uuid4(),
        slug="lake",
        name="lake",
        kind="ducklake",
        metadata_schema="lake",
        created_by=ana.id,
    )
    iceberg.storage_backend = lake.storage_backend = backend
    async with factory() as db:
        db.add_all([ana, workspace])
        await db.flush()
        db.add_all([backend, iceberg, lake])
        await db.flush()
        db.add_all(
            [
                WorkspaceCatalog(workspace_id=workspace.id, catalog_id=iceberg.id, is_default=True),
                WorkspaceCatalog(
                    workspace_id=workspace.id, catalog_id=lake.id, access_mode="scoped"
                ),
                WorkspaceMember(workspace_id=workspace.id, user_id=ana.id, role="reader"),
                CatalogGrant(user_id=ana.id, catalog_id=lake.id, schema_name="main", tier="reader"),
                TableMetadata(
                    catalog_id=iceberg.id,
                    schema_name="analytics",
                    table_name="orders",
                    owner_id=ana.id,
                    row_count=42,
                    last_write_at=datetime(2026, 11, 1, tzinfo=UTC),
                ),
            ]
        )
        await db.commit()
    return workspace, ana, iceberg, lake


async def test_the_publisher_copies_attachments_members_and_grants(system_store):
    assert await store.ensure(system_store)
    workspace, ana, _iceberg, _lake = await _control_plane(system_store)

    await publisher.publish_once(system_store)

    async with store.get_engine().connect() as conn:
        catalogs = (await conn.execute(sa.select(store.INFO_CATALOGS))).all()
        scopes = (await conn.execute(sa.select(store.ACL_SCOPES))).all()
        members = (await conn.execute(sa.select(store.ACL_MEMBERS))).all()
    assert {(c.catalog_name, c.catalog_kind, c.access_mode, c.is_default) for c in catalogs} == {
        ("sales", "iceberg_polaris", "open", True),
        ("lake", "ducklake", "scoped", False),
    }
    assert [(s.user_id, s.schema_name, s.table_name) for s in scopes] == [(ana.id, "main", None)]
    assert [(m.workspace_id, m.user_id) for m in members] == [(workspace.id, ana.id)]
    assert (await _seen(system_store, workspace.id, ana.id))["catalogs"] == {"sales", "lake"}


def _table(
    schema: str, name: str, *columns: tuple[str, str, str], fmt="ICEBERG"
) -> CatalogTableInfo:
    return CatalogTableInfo(
        name=name,
        catalog_name="x",
        schema_name=schema,
        data_source_format=fmt,
        columns=[
            CatalogColumnInfo(
                name=n, type_text=src, type_name=src.upper(), position=i, duckdb_type=duck
            )
            for i, (n, src, duck) in enumerate(columns)
        ],
        current_snapshot_summary={"total-records": "7", "total-files-size": "1024"},
    )


class _FakeBackend:
    """A catalog's metadata as a test sets it, counting each read."""

    def __init__(self) -> None:
        self.tables: dict[tuple[str, str], CatalogTableInfo] = {}
        self.schemas: set[str] = set()
        self.routines: list[CatalogRoutineInfo] = []
        self.whole_reads = 0
        self.table_reads: list[tuple[str, str]] = []

    async def describe_catalog(self, catalog) -> CatalogDescription:
        self.whole_reads += 1
        return CatalogDescription(
            schemas=[
                CatalogSchemaInfo(name=s, catalog_name=catalog.slug) for s in sorted(self.schemas)
            ],
            tables=list(self.tables.values()),
            routines=self.routines,
        )

    async def get_table(self, catalog, schema, name) -> CatalogTableInfo:
        self.table_reads.append((schema, name))
        if (schema, name) not in self.tables:
            raise CatalogBackendNotFound(f"{schema}.{name}")
        return self.tables[schema, name]


@pytest.fixture
async def crawled(system_store, monkeypatch):
    """The store, a control plane, and a fake backend per catalog."""
    assert await store.ensure(system_store)
    workspace, ana, iceberg, lake = await _control_plane(system_store)
    backends = {iceberg.id: _FakeBackend(), lake.id: _FakeBackend()}
    monkeypatch.setattr(metadata, "backend_for", lambda catalog, polaris: backends[catalog.id])
    backends[iceberg.id].schemas = {"analytics", "empty"}
    backends[iceberg.id].tables[("analytics", "orders")] = _table(
        "analytics",
        "orders",
        ("id", "long", "BIGINT"),
        ("total", "decimal(10, 2)", "DECIMAL(10,2)"),
    ).model_copy(update={"last_modified": datetime(2026, 11, 2, tzinfo=UTC)})
    backends[lake.id].schemas = {"main"}
    backends[lake.id].tables[("main", "t")] = _table(
        "main", "t", ("x", "int32", "INTEGER"), fmt="DUCKLAKE"
    )

    async def tick(now: datetime | None = None) -> bool:
        return await metadata.refresh_once(system_store, polaris=None, now=now)

    async def finished(sql: str, **fields) -> None:
        async with system_store() as db:
            db.add(
                Query(
                    workspace_id=workspace.id,
                    user_id=ana.id,
                    sql=sql,
                    status="done",
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                    **fields,
                )
            )
            await db.commit()

    async def rows(table: sa.Table) -> list[sa.Row]:
        async with store.get_engine().connect() as conn:
            return list((await conn.execute(sa.select(table))).all())

    return backends, iceberg, lake, tick, finished, rows


async def test_each_catalog_is_read_whole_with_what_duckhaven_records_of_its_tables(crawled):
    backends, iceberg, lake, tick, _finished, rows = crawled

    assert await tick()

    schemata = {(r.catalog_name, r.schema_name) for r in await rows(store.INFO_SCHEMATA)}
    assert schemata == {("sales", "analytics"), ("sales", "empty"), ("lake", "main")}
    (orders,) = [r for r in await rows(store.INFO_TABLES) if r.table_name == "orders"]
    assert (orders.row_count, orders.bytes, orders.table_owner, orders.column_count) == (
        42,
        1024,
        "ana@example.com",
        2,
    )
    # Iceberg's own record of its last change is later than DuckHaven's.
    assert orders.last_altered == datetime(2026, 11, 2, tzinfo=UTC)
    columns = sorted(
        (r.table_name, r.ordinal_position, r.column_name, r.data_type, r.source_type, r.is_nullable)
        for r in await rows(store.INFO_COLUMNS)
    )
    assert columns == [
        ("orders", 1, "id", "BIGINT", "long", "YES"),
        ("orders", 2, "total", "DECIMAL(10,2)", "decimal(10, 2)", "YES"),
        ("t", 1, "x", "INTEGER", "int32", "YES"),
    ]
    # Not read again until it is due, or something changes it.
    assert await tick()
    assert [b.whole_reads for b in backends.values()] == [1, 1]


async def test_a_table_a_statement_creates_or_drops_is_read_alone(crawled):
    backends, iceberg, _lake, tick, finished, rows = crawled
    await tick()
    ice = backends[iceberg.id]
    ice.tables[("analytics", "refunds")] = _table("analytics", "refunds", ("id", "long", "BIGINT"))

    await finished("CREATE TABLE analytics.refunds (id BIGINT);")
    await finished("SELECT * FROM analytics.orders;")
    await tick()

    assert ice.whole_reads == 1
    assert ice.table_reads == [("analytics", "refunds")]
    assert {r.table_name for r in await rows(store.INFO_TABLES)} == {"orders", "refunds", "t"}

    del ice.tables[("analytics", "orders")]
    await finished("DROP TABLE ANALYTICS.ORDERS;")
    await tick()

    assert ice.whole_reads == 1
    assert {r.table_name for r in await rows(store.INFO_TABLES)} == {"refunds", "t"}
    assert {r.table_name for r in await rows(store.INFO_COLUMNS)} == {"refunds", "t"}
    assert await rows(store.METADATA_CHANGES) == []


async def test_a_schema_change_or_a_ducklake_change_reads_the_catalog_whole(crawled):
    backends, iceberg, lake, tick, finished, _rows = crawled
    await tick()

    await finished("CREATE SCHEMA staging;")
    await finished("INSERT INTO lake.main.t VALUES (1);")
    await tick()

    assert backends[iceberg.id].whole_reads == 2
    assert backends[lake.id].whole_reads == 2


async def test_a_macro_is_listed_and_goes_with_its_catalog_s_next_read(crawled):
    backends, _iceberg, lake, tick, finished, rows = crawled
    backends[lake.id].routines = [
        CatalogRoutineInfo("main", "add_tax", 0, "macro", ["x", "rate"], "(x * (1 + rate))"),
        CatalogRoutineInfo("main", "recent", 0, "table_macro", ["n INTEGER"], "SELECT 1"),
    ]
    await tick()

    assert {
        (r.catalog_name, r.routine_name, r.routine_type, r.parameters)
        for r in await rows(store.INFO_ROUTINES)
    } == {
        ("lake", "add_tax", "macro", "x, rate"),
        ("lake", "recent", "table_macro", "n INTEGER"),
    }

    backends[lake.id].routines = backends[lake.id].routines[1:]
    await finished("DROP MACRO lake.main.add_tax;")
    await tick()

    assert [r.routine_name for r in await rows(store.INFO_ROUTINES)] == ["recent"]


async def test_a_rest_change_is_read_on_the_next_tick(crawled):
    backends, iceberg, _lake, tick, _finished, rows = crawled
    await tick()
    store_ready = store.is_available()
    backends[iceberg.id].tables[("empty", "fresh")] = _table(
        "empty", "fresh", ("id", "long", "BIGINT")
    )

    await metadata.mark_changed(iceberg.id, "empty", "fresh")
    await tick()

    assert store_ready
    assert "fresh" in {r.table_name for r in await rows(store.INFO_TABLES)}


async def test_a_catalog_due_again_is_read_whole_and_a_deleted_one_goes(crawled, system_store):
    backends, iceberg, lake, tick, _finished, rows = crawled
    await tick()

    await tick(datetime.now(UTC) + timedelta(hours=1))
    assert backends[iceberg.id].whole_reads == 2

    async with system_store() as db:
        await db.execute(sa.delete(WorkspaceCatalog).where(WorkspaceCatalog.catalog_id == lake.id))
        await db.execute(sa.delete(CatalogGrant).where(CatalogGrant.catalog_id == lake.id))
        await db.execute(sa.delete(Catalog).where(Catalog.id == lake.id))
        await db.commit()
    await tick()

    assert {r.catalog_name for r in await rows(store.INFO_TABLES)} == {"sales"}


async def test_one_replica_reads_at_a_time(crawled):
    backends, iceberg, _lake, tick, _finished, _rows = crawled
    async with store.get_engine().connect() as other:
        await other.execute(sa.text("SELECT pg_advisory_lock(:k)"), {"k": metadata._LOCK_KEY})
        assert await tick() is False
        await other.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": metadata._LOCK_KEY})
    assert backends[iceberg.id].whole_reads == 0


async def test_a_catalog_that_cannot_be_read_does_not_hold_up_the_others(crawled):
    backends, iceberg, _lake, tick, _finished, rows = crawled
    attempts = []

    async def unreachable(catalog):
        attempts.append(catalog.slug)
        raise RuntimeError("connection refused")

    backends[iceberg.id].describe_catalog = unreachable

    assert await tick()
    assert await tick()

    assert {r.catalog_name for r in await rows(store.INFO_TABLES)} == {"lake"}
    # Tried again when it is next due, not on every tick.
    assert attempts == ["sales"]
