# Metadata

DuckHaven splits its state across two stores, and the split is an
[architectural invariant](architecture.md#7-architectural-invariants): catalog structure lives in the catalog's own
metastore, and DuckHaven's own entities live in Postgres.

## Who owns what

- **The catalog metastore** owns catalog *structure* — schemas, tables, columns, and snapshot history. Which metastore
  that is depends on the catalog's [kind](catalogs.md#catalog-kinds): [Polaris](catalogs.md) for an Iceberg catalog, or
  a schema of `ducklake_*` tables in the `ducklake` database for a [DuckLake](ducklake.md) one. DuckHaven reads
  structure and snapshot history live and never persists either.
- **Postgres** owns DuckHaven entities — users, workspaces, members, agents, queries, and saved queries.

!!! note "Two things in one Postgres instance"
    A DuckLake catalog's metastore *is* SQL, and the bundled deployment puts it in the same Postgres server as
    DuckHaven's own tables — in its own database, which DuckHaven's migrations do not manage. That is the same
    relationship the Polaris metastore has always had with that server. Colocation is not ownership: the `ducklake`
    database is the authority for a DuckLake catalog's structure, and DuckHaven's schema still mirrors none of it.

## The table-metadata sidecar

DuckHaven's own database keeps a small `table_metadata` sidecar for facts no metastore tracks, keyed by the
schema/table name:

- **Ownership** and **last-write provenance** (who wrote a table last, when, and from which agent).
- **Agent-computed stats** — row count and size in bytes.
- **Table-format facts** mirrored for display — the data-file count, whether delete files are present, and the
  snapshot the table last changed in. Recorded for both kinds. For DuckLake that snapshot id is a *catalog* snapshot:
  a DuckLake commit covers the whole catalog, so the id names the commit in which this table last changed rather than a
  lineage of its own.

The sidecar is populated when a table is created and refreshed when sample/stats run. The metastore always remains the
source of truth for catalog structure — the sidecar never becomes a catalog cache.

## Querying metadata as SQL

The same structure is queryable read-only from a worksheet through each catalog's built-in `information_schema`. This is
not a second copy of the metadata: it is DuckDB's native, live projection of the catalogs attached to your query —
computed per query, never cached — so the metastore stays the single source of truth. See
[Inspecting metadata](../reference/sql-support.md#inspecting-metadata-information_schema) for the supported views
and the `DESCRIBE` path for columns.

## Related

- [Catalogs](catalogs.md) — the structure authority, and the two kinds it comes in.
- [Tables & Iceberg](tables.md) — what the sidecar describes.
- [SQL support](../reference/sql-support.md) — querying metadata with `information_schema`.
- [Semantic layer](semantic-layer.md) — business meaning layered over this structure. Kept per *workspace* rather than
  per catalog, because what a term means is an organizational decision rather than an intrinsic fact about the data.
