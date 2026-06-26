# Catalogs & Polaris

DuckHaven uses [Apache Polaris](https://polaris.apache.org/) as the authority for catalog structure and as the vendor
of short-lived storage credentials. A **catalog** is a first-class, decoupled entity — a data domain with its own
Polaris catalog and [storage backend](storage-backends.md) — that is attached to one or more
[workspaces](workspaces.md) (a many-to-many relationship, like Databricks' Unity Catalog).

## Catalogs are decoupled and shareable

- A catalog has a globally-unique, identifier-safe **slug** (used in `catalog.schema.table` SQL and as the DuckDB
  attach alias) and a **Polaris name** (the warehouse). It owns its storage backend.
- A workspace attaches any number of catalogs; one is the **default** (used for unqualified names). The **same catalog
  can be attached to multiple workspaces**, so a shared `raw` catalog can appear in both `dev` and `prod`.
- The default namespace is **`analytics`**, not `main` — `main` is DuckDB's built-in default schema and would shadow the
  Iceberg namespace.
- Tables are [Apache Iceberg](tables.md) and catalog-managed: Polaris arbitrates every commit.

## Querying across catalogs

When a query runs, the agent attaches **every catalog bound to the workspace**, each under its slug alias, and `USE`s
the active catalog. So unqualified names resolve against the active catalog, and a query can join across catalogs with
fully-qualified `catalog.schema.table` references:

```sql
SELECT *
FROM raw.analytics.events e
JOIN curated.analytics.users u ON e.user_id = u.id;
```

The active catalog is chosen per worksheet; existing single-catalog SQL keeps working unchanged.

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

Polaris vends short-lived, connection-scoped storage credentials per catalog when an [agent](agents.md) attaches it. No
long-lived storage secrets are stored on agents. See [Storage backends](storage-backends.md).

## Related

- [Tables & Iceberg](tables.md) — what lives inside a catalog.
- [Manage catalogs](../guides/manage-catalogs.md) — create, attach, detach, and drop catalogs and their schemas/tables.
