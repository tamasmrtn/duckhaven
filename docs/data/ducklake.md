---
type: concept
---

# DuckLake

A DuckLake catalog keeps its metadata in PostgreSQL and its data as Parquet in object storage; only DuckDB can read it,
but DuckHaven can maintain it.

DuckLake is the second kind of [catalog](catalogs.md) DuckHaven can create. An Iceberg catalog keeps its metadata in
Apache Polaris; a DuckLake catalog keeps it in SQL tables in a PostgreSQL database, and its data in Parquet in the same
[object storage](storage-backends.md) everything else uses. Iceberg with Polaris is the default kind for a new
catalog. DuckLake matches it on every capability DuckHaven exposes, and exceeds it on maintenance, which DuckDB can run
for DuckLake but not for Iceberg.

!!! info "Availability"
    On by default; an operator can [turn it off](../deployment/ducklake.md). Creating a DuckLake catalog needs the
    workspace `owner` role, and an agent with the `ducklake` and `postgres` extensions.

## The trade-off, stated plainly

This is the decision that matters, and it cannot be reversed later without copying data:

| | Apache Iceberg + Polaris | DuckLake |
|---|---|---|
| Read by other engines | Spark, Trino, Flink, PyIceberg | **DuckDB only** — but [exportable](#exporting-to-iceberg) |
| Catalog metadata | Polaris (a service) | SQL tables in Postgres |
| Data files | Parquet | Parquet |
| Snapshots | Per table | **Per catalog** |
| Maintenance | Advisory only — DuckDB cannot run it | **DuckHaven runs it for you** |
| Storage migration | Supported | Supported |
| Services required | Polaris + its database | None beyond Postgres |

The first row is the one to weigh. An Iceberg table is portable: any engine that speaks the format can open it, which
is what makes "no lock-in" a real claim rather than a slogan. A DuckLake table can be opened by DuckDB and nothing
else. Choosing DuckLake trades that portability for a simpler deployment and maintenance DuckHaven runs for you.

The create dialog says so at the moment you choose. It is repeated here because it is easy to skip past.

## What you get in exchange

- **One fewer service.** A deployment that uses only DuckLake catalogs does not need Polaris at all — see
  [Enable DuckLake](../deployment/ducklake.md#a-ducklake-only-deployment).
- **Faster browsing.** Listing schemas, tables and columns is a SQL query against Postgres, with no REST round-trip and
  no agent involved. A DuckLake catalog can be browsed even when no [agent](../compute/agents.md) is connected, and a
  table's size is read from the catalog rather than measured — the catalog keeps a running total of its live data files,
  so the number is exact and costs nothing. An Iceberg table's size needs an agent to go and look.
- **Maintenance that can actually run.** DuckDB's `ducklake` extension performs compaction, snapshot expiry and orphan
  cleanup; its `iceberg` extension cannot. The [maintenance advisor](maintenance.md) scans both kinds, and a DuckLake
  table's recommendations name a `ducklake_*` command DuckDB itself can run rather than an external engine. Its health
  numbers are also cheaper and exact: file sizes are columns in the catalog database, so there are no Parquet footers
  to read.

    !!! note "And DuckHaven can run it"
        A DuckLake recommendation carries an **Apply** button, which dispatches the command shown on the card. It
        needs the `maintenance:manage` permission, it is manual rather than scheduled, and two of the verbs act on the
        whole catalog rather than one table — see [Applying maintenance](maintenance.md#applying-maintenance).

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
Iceberg one does, and the same [invariant](../developer/architecture.md#architectural-invariants) holds:
one catalog, one backend. A workspace can attach catalogs of both kinds and join across them in a single query with
fully-qualified names.

### Where the metadata lives

Each DuckLake catalog owns one PostgreSQL schema — `cat_<slug>` — in a dedicated `ducklake` database, alongside the
`duckhaven` database that holds DuckHaven's own state and the `polaris` database Polaris uses. The
`ducklake_*` tables inside it are defined by the [DuckLake specification](https://ducklake.select/docs/stable/), not by
DuckHaven, and are created by the DuckDB extension the first time an agent attaches the catalog.

DuckHaven reads those tables directly to browse a catalog. It never writes them by hand: every change goes through the
extension, because only the extension implements DuckLake's transaction protocol.

The layout of those tables is versioned with the extension, and migrating it is one-way: a newer `ducklake` can
upgrade an older catalog, and an older one can't read the result. DuckHaven never asks for that upgrade. When agents
on different [runtimes](../compute/runtimes.md) share a catalog, see
[Mixing runtimes on the same data](../compute/runtimes.md#mixing-runtimes-on-the-same-data).

## Snapshots are catalog-wide

An Iceberg snapshot belongs to one table. A **DuckLake snapshot is a commit against the whole catalog** — creating a
schema, inserting into a table and dropping another are each one snapshot of the catalog, numbered in a single
sequence.

A table's **History** tab therefore shows the catalog snapshots in which *that table* changed, and says so. Time travel
works the same way as for Iceberg, with DuckLake's syntax:

```sql
SELECT * FROM raw.analytics.events AT (VERSION => 42);
SELECT * FROM raw.analytics.events AT (TIMESTAMP => TIMESTAMP '2026-09-01 00:00:00');
```

!!! note "Very small writes are stored in the catalog, not in a file"
    DuckLake keeps changes below `DUCKLAKE_DATA_INLINING_ROW_LIMIT` (ten rows by default) in the catalog database
    instead of writing a Parquet file. Those snapshots still appear in the table's history — DuckHaven reads the
    catalog's own record of which table each one changed — but they contribute no data file, so they do not move the
    file counts on the [maintenance](maintenance.md) page. A table holding only such rows shows its size as
    **Inlined** in the catalog browser, since it has no data files to measure.

## What DuckLake cannot do

From the [specification's own list](https://ducklake.select/docs/stable/duckdb/unsupported_features):

- No indexes, and no enforced primary-key, unique or foreign-key constraints.
- No `ARRAY` (fixed-size, such as `INTEGER[3]`), `ENUM`, `UNION`, `VARINT` or `BITSTRING` columns. A `CREATE TABLE`
  with one fails with `Failed to convert DuckDB type to DuckLake - unsupported type …`. Variable-length lists
  (`INTEGER[]`) work.
- No sequences, and no non-literal column defaults.
- `MERGE INTO` is the only upsert.

Not the specification's fault, but worth knowing:

- **Partitioning, sort order and encryption are not surfaced by DuckHaven.** DuckLake supports all three — including
  Iceberg-compatible `bucket(N, column)` transforms and per-file encryption with keys held in the catalog database —
  and DuckHaven's create-table surface offers none of them, for either catalog kind. This is a gap in DuckHaven rather
  than in DuckLake.
- **Creating a schema or table needs a connected [agent](../compute/agents.md).** Browsing does not: the catalog is read
  straight from PostgreSQL. But only the DuckDB extension can commit a change, so DDL is dispatched to compute. An
  Iceberg catalog's DDL is a REST call the control plane makes on its own.

Within DuckHaven specifically:

- **Storage migration refuses files registered in place.** `ducklake_add_data_files` records an absolute path, which
  does not move when the catalog's data path changes. DuckHaven refuses rather than guessing — see
  [Migrate a catalog's storage](migrate-catalog-storage.md). A catalog DuckHaven wrote itself has none.
- **`information_schema` does not work** against an attached DuckLake catalog, exactly as it does not for an attached
  Iceberg catalog. Use `DESCRIBE`.

## Exporting to Iceberg

The DuckDB-only limitation can be undone. An export creates a new [Iceberg catalog](catalogs.md#catalog-kinds) and
copies the whole DuckLake catalog into it, in one `COPY FROM DATABASE` statement. The result is an ordinary Iceberg
catalog: Spark, Trino, Flink and PyIceberg can open it, and DuckHaven treats it like any other.

Start an export with the [command line](../reference/cli.md), `dh` 0.3.0 or later, as the catalog's creator or with
`catalogs:admin`, and the `owner` role in the workspace the new catalog joins:

```bash
dh catalog export create ⟨catalog id⟩ --target-name ⟨new catalog name⟩ --storage-backend ⟨backend id⟩
dh catalog export list ⟨catalog id⟩
```

The new catalog is attached to your current workspace, on the storage backend you choose. The export runs in the
background and appears in [History](../querying/run-queries.md#find-an-earlier-run) under your name. The web app has no
export button; the REST endpoint is `POST /api/catalogs/{catalog_id}/exports`.

!!! warning "The copy carries state, not history"
    It copies the catalog's **current** contents. Snapshots, and therefore [time travel](tables.md) into the past, do
    not cross — the exported tables start with a single snapshot of the data as it was when the export ran. The
    confirmation says so before you start.

The trade-off itself stays: in place, only DuckDB reads a DuckLake catalog. But choosing DuckLake is not a choice made
once and forever.

!!! note "One direction only"
    Iceberg → DuckLake is a different problem and is not supported; see below.

## Converting an existing Iceberg catalog

**Not supported**, in this direction. There is no way to turn an existing Iceberg catalog into a DuckLake one, in
DuckHaven or outside it. (The reverse — DuckLake to Iceberg — is [supported](#exporting-to-iceberg).)

DuckDB's `iceberg_to_ducklake()` is designed for exactly this — a metadata-only copy that carries snapshot history
across without moving a byte of Parquet — but at the versions DuckHaven ships (DuckLake 1.0 on DuckDB 1.5.5) it
refuses to run:

```text
Invalid Input Error: 'iceberg_to_ducklake' only support version 0.4 currently, detected '1.0' instead
```

This is an upstream limitation, tracked as [duckdb/ducklake#1278](https://github.com/duckdb/ducklake/issues/1278).
DuckHaven does not hand-roll a replacement, because one would lose the snapshot history, which is most of the point.

To move data between kinds, create the new catalog and copy with SQL:

```sql
CREATE TABLE lake.analytics.events AS SELECT * FROM raw.analytics.events;
```

That rewrites the data and starts fresh history. Verify it, then drop the source catalog — which purges its files, so
be sure first.

## Governance is unchanged

Everything DuckHaven enforces, it enforces the same way for both kinds, because none of it depends on the table format:

- [Workspace roles and scoped grants](../access/how-access-works.md) are checked at the API before a query is
  dispatched. DuckLake itself has no access control of its own, which does not matter here — DuckHaven was never
  delegating authorization to the catalog.
- **Catalogs are isolated from each other in PostgreSQL, not just in SQL.** Each DuckLake catalog has its own database
  login, granted on its own metadata schema and nothing else, so an agent serving one catalog holds no credential that
  reaches another's metadata. See [Enable DuckLake](../deployment/ducklake.md#why-the-isolation-matters).
- The [audit trail](../querying/session-audit.md), [lineage](../metadata/lineage.md) and query history all work
  identically.
- The SQL allowlist is unchanged, and additionally refuses any statement that reaches DuckLake's internal metadata
  tables or opens a connection to a foreign database.

## Related

- [Enable DuckLake](../deployment/ducklake.md) — turning it on, and what it needs from Postgres.
- [Create a DuckLake catalog](create-a-ducklake-catalog.md) — the operator walkthrough.
- [Catalogs](catalogs.md) — how a catalog of either kind binds to a workspace.
- [Storage backends](storage-backends.md) — the orthogonal axis.
