# DuckLake

**DuckLake** is the second kind of [catalog](catalogs.md) DuckHaven can create. Where an Iceberg catalog keeps its
metadata in Apache Polaris, a DuckLake catalog keeps it in SQL tables in a PostgreSQL database, and its data in plain
Parquet in the same [object storage](storage-backends.md) everything else uses.

It is **off by default** and **experimental**. Apache Iceberg + Polaris remains the default kind, and existing catalogs
are untouched by this feature existing.

## The trade-off, stated plainly

This is the decision that matters, and it cannot be reversed later without copying data:

| | Apache Iceberg + Polaris | DuckLake |
|---|---|---|
| Read by other engines | Spark, Trino, Flink, PyIceberg | **DuckDB only** |
| Catalog metadata | Polaris (a service) | SQL tables in Postgres |
| Data files | Parquet | Parquet |
| Snapshots | Per table | **Per catalog** |
| Maintenance | Advisory only — DuckDB cannot run it | DuckDB can run it |
| Storage migration | Supported | Not supported |
| Services required | Polaris + its database | None beyond Postgres |

The first row is the one to weigh. An Iceberg table is portable: any engine that speaks the format can open it, which
is what makes "no lock-in" a real claim rather than a slogan. A DuckLake table, today, can be opened by DuckDB and
nothing else. Choosing DuckLake trades that portability for a simpler deployment and maintenance DuckHaven can
eventually run for you.

The create dialog says so at the moment you choose. It is repeated here because it is easy to skip past.

## What you get in exchange

- **One fewer service.** A deployment that uses only DuckLake catalogs does not need Polaris at all — see
  [Enable DuckLake](../deployment/ducklake.md#a-ducklake-only-deployment).
- **Faster browsing.** Listing schemas, tables and columns is a SQL query against Postgres, with no REST round-trip
  and no agent involved. A DuckLake catalog can be browsed even when no [agent](agents.md) is connected.
- **Maintenance that can actually run.** DuckDB's `ducklake` extension performs compaction, snapshot expiry and orphan
  cleanup; its `iceberg` extension cannot. The [maintenance advisor](maintenance.md) scans both kinds, and a DuckLake
  table's recommendations name a `ducklake_*` command DuckDB itself can run rather than an external engine. Its health
  numbers are also cheaper and exact: file sizes are columns in the catalog database, so there are no Parquet footers
  to read.

    !!! note "Still advisory in this release"
        DuckHaven names the command a DuckLake catalog needs and tells you DuckDB can run it, but does not run it for
        you yet. Executing maintenance needs its own design — who may trigger it, what it locks, how it is audited —
        and that is not part of this release.

## How it is put together

```text
      DuckHaven control plane
                |
        catalog kind
        /              \
  iceberg_polaris     ducklake
        |                |
    Polaris          ducklake_* tables
   (own database)    (one schema per catalog,
                      in the `ducklake` database)
        \              /
         object storage
    (bundled / S3 / ADLS Gen 2)
                |
        DuckDB agents
```

**Catalog kind and storage backend are independent.** A DuckLake catalog binds to a storage backend exactly as an
Iceberg one does, and the same [invariant](architecture.md#7-architectural-invariants) holds: one catalog, one backend.
A workspace can attach catalogs of both kinds and join across them in a single query with fully-qualified names.

### Where the metadata lives

Each DuckLake catalog owns one PostgreSQL schema — `cat_<slug>` — in a dedicated `ducklake` database, alongside the
`duckhaven` database that holds DuckHaven's own state and the `polaris` database Polaris has always used. The
`ducklake_*` tables inside it are defined by the [DuckLake specification](https://ducklake.select/docs/stable/), not by
DuckHaven, and are created by the DuckDB extension the first time an agent attaches the catalog.

DuckHaven reads those tables directly to browse a catalog. It never writes them by hand: every change goes through the
extension, because only the extension implements DuckLake's transaction protocol.

## Snapshots are catalog-wide

An Iceberg snapshot belongs to one table. A **DuckLake snapshot is a commit against the whole catalog** — creating a
schema, inserting into a table and dropping another are each one snapshot of the catalog, numbered in a single
sequence.

A table's Snapshots tab therefore shows the catalog snapshots in which *that table* changed, and says so. Time travel
works the same way as for Iceberg, with DuckLake's syntax:

```sql
SELECT * FROM raw.analytics.events AT (VERSION => 42);
SELECT * FROM raw.analytics.events AT (TIMESTAMP => TIMESTAMP '2026-09-01 00:00:00');
```

!!! note "Small writes may not appear in a table's history"
    DuckLake stores very small changes (ten rows by default) in the catalog database rather than writing a Parquet
    file. Those snapshots exist and are queryable by version, but they leave no file for DuckHaven to attribute to a
    table, so they do not appear in the table's snapshot list.

## What DuckLake cannot do

From the [specification's own list](https://ducklake.select/docs/stable/duckdb/unsupported_features):

- No indexes, and no enforced primary-key, unique or foreign-key constraints.
- No `ARRAY`, `ENUM`, `UNION`, `VARINT` or `BITSTRING` columns. DuckHaven refuses to create these with a clear error
  rather than letting the statement fail partway through.
- No sequences, and no non-literal column defaults.
- `MERGE INTO` is the only upsert.

Within DuckHaven specifically:

- **No storage migration.** Moving a catalog between backends rewrites the absolute file URIs Iceberg embeds in its
  metadata. DuckLake records relative paths, so it needs a different (simpler) procedure that does not exist yet.
  DuckLake catalogs are not offered in Admin → Migrations.
- **`information_schema` does not work** against an attached DuckLake catalog, exactly as it does not for an attached
  Iceberg catalog. Use `DESCRIBE`.

## Converting an existing Iceberg catalog

**Not supported.** There is no way to turn an existing Iceberg catalog into a DuckLake one, in DuckHaven or outside it.

DuckDB's `iceberg_to_ducklake()` is designed for exactly this — a metadata-only copy that carries snapshot history
across without moving a byte of Parquet — but at the versions DuckHaven ships (DuckLake 1.0 on DuckDB 1.5.5) it
refuses to run:

```text
Invalid Input Error: 'iceberg_to_ducklake' only support version 0.4 currently, detected '1.0' instead
```

This is an upstream limitation, tracked as [duckdb/ducklake#1278](https://github.com/duckdb/ducklake/issues/1278).
When it is fixed, conversion becomes worth building; until then anything DuckHaven offered would be a hand-rolled
reimplementation that loses the snapshot history, which is most of the point.

To move data between kinds today, create the new catalog and copy with SQL:

```sql
CREATE TABLE lake.analytics.events AS SELECT * FROM raw.analytics.events;
```

That rewrites the data and starts fresh history. Verify it, then drop the source catalog — which purges its files, so
be sure first.

## Governance is unchanged

Everything DuckHaven enforces, it enforces the same way for both kinds, because none of it depends on the table format:

- [Workspace roles and scoped grants](permissions.md) are checked at the API before a query is dispatched. DuckLake
  itself has no access control of its own, which does not matter here — DuckHaven was never delegating authorization
  to the catalog.
- The [audit trail](../guides/session-audit.md), [lineage](lineage.md) and query history all work identically.
- The SQL allowlist is unchanged, and additionally refuses any statement that reaches DuckLake's internal metadata
  tables or opens a connection to a foreign database.

## Related

- [Enable DuckLake](../deployment/ducklake.md) — turning it on, and what it needs from Postgres.
- [Create a DuckLake catalog](../guides/create-a-ducklake-catalog.md) — the operator walkthrough.
- [Catalogs](catalogs.md) — how a catalog of either kind binds to a workspace.
- [Storage backends](storage-backends.md) — the orthogonal axis.
