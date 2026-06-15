# Tables & Iceberg

Every table DuckHaven creates is an [Apache Iceberg](https://iceberg.apache.org/) table, catalog-managed by
[Polaris](catalogs.md). Iceberg gives DuckHaven snapshots, schema evolution, and time-travel queries out of the box.

## Creating tables

Tables can be created two ways, both backed by Polaris:

- **From the catalog UI** — a dialog where you specify columns and types.
- **From SQL** — a `CREATE TABLE` statement run through a worksheet against the attached catalog.

The breadth of `CREATE` / `ALTER` support is bounded by the DuckDB `iceberg` extension version on the executing
[agent](agents.md); unsupported operations surface as query errors rather than silent no-ops.

## Snapshots and time travel

Each table keeps an Iceberg **snapshot history**. DuckHaven reads it live from Polaris (it is never persisted) and lets
you open a worksheet pinned to a past snapshot using DuckDB's time-travel syntax. See
[Snapshots & time travel](../guides/snapshots-time-travel.md).

!!! note "No snapshot expiration yet"
    DuckHaven browses and queries snapshot history but does **not** currently expire or compact old snapshots — that is
    a roadmap item, not a shipped feature.

## Dropping tables

`DROP TABLE` purges the underlying data files (Polaris drop-with-purge is enabled on DuckHaven-owned catalogs).

## Sample rows and stats

The catalog browser can preview sample rows (capped, run as an internal query excluded from history) and shows
agent-computed row counts and size, plus Iceberg facts like the latest snapshot and whether delete files are present.
See [Metadata](metadata.md).
