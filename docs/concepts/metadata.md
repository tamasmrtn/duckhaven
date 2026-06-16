# Metadata

DuckHaven splits its state across two stores, and the split is an
[architectural invariant](architecture.md#11-architectural-invariants): catalog structure lives in
[Polaris](catalogs.md), and DuckHaven's own entities live in Postgres.

## Who owns what

- **Polaris** owns catalog *structure* — namespaces, tables, columns, and Iceberg snapshot history. DuckHaven reads
  snapshot history live and never persists it.
- **Postgres** owns DuckHaven entities — users, workspaces, members, agents, queries, and saved queries.

## The table-metadata sidecar

Postgres also keeps a small `table_metadata` sidecar for facts Polaris does not track, keyed by the Polaris
schema/table name:

- **Ownership** and **last-write provenance** (who wrote a table last, when, and from which agent).
- **Agent-computed stats** — row count and size in bytes.
- **Iceberg facts** mirrored for display — latest snapshot id and whether delete files are present.

The sidecar is populated when a table is created and refreshed when sample/stats run. Polaris always remains the source
of truth for catalog structure — the sidecar never becomes a catalog cache.

## Related

- [Catalogs & Polaris](catalogs.md) — the structure authority.
- [Tables & Iceberg](tables.md) — what the sidecar describes.
