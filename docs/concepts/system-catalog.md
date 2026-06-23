# System catalog

DuckHaven ships a built-in, **read-only** catalog that exposes metadata and
historical activity about DuckHaven itself — query history, audit events, and an
object inventory — **across all workspaces**. It is modeled on Databricks'
`system` catalog and Snowflake's `SNOWFLAKE` database: a place you can *query*
operational data with ordinary SQL.

It is attached to every workspace automatically. You never attach it, and it
cannot be detached or dropped.

## Why it is called `duckhaven` in SQL

DuckDB reserves `system` as a catalog name, so the system catalog is addressed
as **`duckhaven`** in SQL:

```sql
SELECT * FROM duckhaven.query.history WHERE status = 'failed';
SELECT * FROM duckhaven.access.audit  WHERE action = 'DROP';
SELECT * FROM duckhaven.info_schema.tables WHERE catalog = 'raw';
```

The UI labels it the *system catalog*; only the SQL identifier is `duckhaven`.

## What it contains (v1)

| Schema | Table | Grain | Source |
|---|---|---|---|
| `query` | `history` | one row per finished query | the Postgres query log |
| `access` | `audit` | one event per finished query | derived from the same query rows |
| `info_schema` | `catalogs`, `schemas`, `tables`, `columns` | current-state snapshot | Apache Polaris + the metadata sidecar |

The full column list is in the [system catalog reference](../reference/system-catalog.md).

`info_schema` is DuckHaven's cross-workspace equivalent of an
`information_schema`. (It is named `info_schema` because DuckDB injects a
built-in `information_schema` into every attached catalog, which would collide.)

!!! note "Bounded latency, not live"
    `query.history` and `access.audit` are a **derived copy** of the
    authoritative Postgres query log, refreshed on a short interval (default 60s,
    set by `SYSTEM_CATALOG_SYNC_INTERVAL_S`). A query you just ran appears within
    a cycle or two — the same model as Snowflake's `ACCOUNT_USAGE`. The
    [query history view](../guides/run-queries.md) in the UI reads Postgres
    directly and is always up to the moment; the system catalog is for ad-hoc
    SQL and cross-workspace analysis.

!!! warning "Scope (v1)"
    The system catalog exposes only what DuckHaven actually produces today.
    `access.audit` records **query activity** (who ran what, and the outcome) —
    it is not yet a full audit of permission or membership changes. Storage and
    compute-usage tables (à la Databricks `system.storage` / `system.compute`)
    are not shipped yet. Rows are **visible to every workspace member**; there is
    no per-row workspace filtering (consistent with DuckHaven's workspace-level
    permission model — see [Permissions](permissions.md)).

## Read-only

The catalog is attached to each query session with DuckDB's `READ_ONLY` flag, so
the engine itself rejects any write or DDL against `duckhaven` — it is not merely
a convention. Reads are unrestricted, including reading the system catalog *into*
a write against one of your own catalogs:

```sql
-- Allowed: read system data, write to a user catalog.
INSERT INTO raw.analytics.slow_queries
SELECT query_id, duration_ms FROM duckhaven.query.history WHERE duration_ms > 60000;

-- Rejected by the engine: the system catalog is read-only.
DROP TABLE duckhaven.query.history;
```

## How it is backed

The system catalog is a real Apache Iceberg catalog in Polaris, so
[agents](agents.md) attach and read it exactly like any other catalog — there is
no special query path. DuckHaven's control plane populates it on a timer using
PyIceberg, writing through Polaris with vended credentials (no long-lived storage
secrets). Its storage backend is chosen by the administrator during first-run
setup (the bundled object store by default, or an external S3/ADLS bucket).

See [Architecture](architecture.md) for how the materializer fits the
control-plane/compute split.

## Related

- [System catalog reference](../reference/system-catalog.md) — every table and column.
- [Catalogs & Polaris](catalogs.md) — how catalogs work in general.
- [Query execution](query-execution.md) — how a query reaches an agent.
