# Tables & Iceberg

Every table in an Iceberg [catalog](catalogs.md) is an [Apache Iceberg](https://iceberg.apache.org/) table,
catalog-managed by Polaris. Iceberg gives DuckHaven snapshots, schema evolution, and time-travel queries out of the
box.

This page describes the default catalog kind. A table in a [DuckLake](ducklake.md) catalog is a DuckLake table
instead — same Parquet underneath, different metadata and a few different limits.

## Creating tables

Tables can be created two ways, whichever kind the catalog is:

- **From the catalog UI** — a dialog where you specify columns and types.
- **From SQL** — a `CREATE TABLE` statement run through a worksheet against the attached catalog.

The breadth of `CREATE` / `ALTER` support is bounded by the DuckDB `iceberg` extension version on the executing
[agent](agents.md); unsupported operations surface as query errors rather than silent no-ops.

## Inspecting a table's columns

Clicking a table in the catalog browser shows its columns as Polaris holds them. From SQL, use
`DESCRIBE <catalog>.<schema>.<table>` — the supported path, and the one every DuckHaven client uses.
`information_schema.columns` cannot introspect an Iceberg table; see
[SQL support](../reference/sql-support.md#columns-and-types-use-describe) for why and what it returns instead.

## Snapshots and time travel

Each table keeps an Iceberg **snapshot history**. DuckHaven reads it live from Polaris (it is never persisted) and lets
you open a worksheet pinned to a past snapshot using DuckDB's time-travel syntax. See
[Snapshots & time travel](../guides/snapshots-time-travel.md).

!!! note "Snapshot expiration is recommended, not yet applied"
    The [maintenance advisor](maintenance.md) detects snapshot bloat, fragmentation, and orphaned files and recommends
    expiring or compacting, with a remediation command for an external engine. It does **not** yet perform these
    operations itself; in-app apply requires native support in the DuckDB `iceberg` extension.

## Dropping tables

`DROP TABLE` purges the underlying data files (Polaris drop-with-purge is enabled on DuckHaven-owned catalogs).

## Sample rows and stats

The catalog browser can preview sample rows (capped, run as an internal query excluded from history) and shows
agent-computed row counts and size, plus format-native facts: the data-file count and whether delete files
are present for either kind, and the latest snapshot for Iceberg tables.
See [Metadata](metadata.md).

## Related

- [Catalogs & Polaris](catalogs.md) — the catalog a table lives in.
- [Metadata](metadata.md) — the per-table facts DuckHaven records alongside Polaris.
- [Snapshots & time travel](../guides/snapshots-time-travel.md) — querying a past snapshot.
- [SQL support](../reference/sql-support.md) — what `CREATE`/`ALTER` and `DESCRIBE` do here.
