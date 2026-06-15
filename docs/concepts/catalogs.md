# Catalogs & Polaris

DuckHaven uses [Apache Polaris](https://polaris.apache.org/) as the authority for catalog structure and as the vendor
of short-lived storage credentials. Each [workspace](workspaces.md) maps to exactly one Polaris catalog.

## One catalog per workspace

- The catalog is named after the workspace slug and contains namespaces (schemas) and tables.
- The default namespace is **`analytics`**, not `main` — `main` is DuckDB's built-in default schema and would shadow the
  Iceberg namespace.
- Tables are [Apache Iceberg](tables.md) and catalog-managed: Polaris arbitrates every commit.

## Polaris owns structure; Postgres does not

DuckHaven never shadows catalog *structure* (schemas, tables, columns) in its own database. Polaris is the source of
truth; Postgres holds only a supplementary [metadata](metadata.md) sidecar for facts Polaris does not track, such as
ownership and last-write provenance. This split is an
[architectural invariant](architecture.md#11-architectural-invariants).

## DuckHaven-owned catalogs

When DuckHaven creates a catalog it grants its service principal the full catalog-management set
(`CATALOG_MANAGE_CONTENT`, `CATALOG_MANAGE_METADATA`, `CATALOG_MANAGE_ACCESS`) and enables drop-with-purge so that
`DROP TABLE` reclaims data files.

## Credential vending

Polaris vends short-lived, connection-scoped storage credentials per `(agent, workspace)` when an
[agent](agents.md) attaches the catalog. No long-lived storage secrets are stored on agents. See
[Storage backends](storage-backends.md).

## Related

- [Tables & Iceberg](tables.md) — what lives inside a catalog.
- [Manage catalogs](../guides/manage-catalogs.md) — create and drop schemas and tables.
