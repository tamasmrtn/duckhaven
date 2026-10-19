---
type: concept
---

# Catalogs

A catalog is a collection of schemas and tables with its own metadata store and storage, which any number of
workspaces can attach.

A catalog holds the data; a [workspace](../access/workspaces.md) only attaches it, and the same catalog can be attached
to several workspaces. Each catalog has a kind, which decides its table format, and a
[storage backend](storage-backends.md), which decides where its files live.

## Catalog kinds

Every catalog has a **kind**, which says where its metadata lives and what format its tables are in. The two are one
choice, not two, because they are not independently selectable.

| Kind | Metadata store | Table format | Readable by |
|---|---|---|---|
| `iceberg_polaris` (default) | [Apache Polaris](https://polaris.apache.org/) | Apache Iceberg | Spark, Trino, Flink, PyIceberg, DuckDB |
| `ducklake` | `ducklake_*` tables in PostgreSQL | [DuckLake](ducklake.md) | DuckDB only |

Iceberg + Polaris is the default kind. DuckLake is available out of the box too; an operator can
[turn it off](../deployment/ducklake.md).

**Kind and storage backend are independent axes.** A catalog of either kind binds to any storage backend, one workspace
can attach catalogs of both kinds, and a single query can join across them.

For Iceberg catalogs, Polaris is the authority for catalog structure and the vendor of short-lived storage
credentials.

## Catalogs are decoupled and shareable

- A catalog has a globally-unique, identifier-safe **slug** (used in `catalog.schema.table` SQL and as the DuckDB
  attach alias) and a **Polaris name** (the warehouse). It owns its storage backend.
- A workspace attaches any number of catalogs; one is the **default** (used for unqualified names). The **same catalog
  can be attached to multiple workspaces**, so a shared `raw` catalog can appear in both `dev` and `prod`.
- The default namespace is **`analytics`**, not `main` — `main` is DuckDB's built-in default schema and would shadow the
  Iceberg namespace.
- Tables are [Apache Iceberg](tables.md) and catalog-managed (Polaris arbitrates every commit), or
  [DuckLake](ducklake.md), depending on the catalog's kind.
- Each attachment has an **access mode**. By default (`open`) every workspace member can read and write the whole
  catalog; switching an attachment to `scoped` narrows access down to the catalog, schema, or table per principal — see
  [scoped access](../access/scoped-access.md).

## Querying across catalogs

When a query runs, the agent attaches the catalogs it uses, each under its slug alias, and `USE`s the active catalog.
Every catalog bound to the workspace is reachable, but only the ones a query names are attached, so a workspace with
many catalogs does not slow down a query that reads one of them (see [which catalogs a query
attaches](../querying/query-execution.md#which-catalogs-a-query-attaches)). Unqualified names resolve against the active
catalog, and a query can join across catalogs with fully-qualified `catalog.schema.table` references:

```sql
SELECT *
FROM raw.analytics.events e
JOIN curated.analytics.users u ON e.user_id = u.id;
```

The active catalog is chosen per worksheet.

## The metastore owns structure; DuckHaven does not

DuckHaven never shadows catalog *structure* (schemas, tables, columns) in its own schema. The catalog's metastore is the
source of truth — Polaris for an Iceberg catalog, the `ducklake` database for a DuckLake one — and DuckHaven's own
database holds only a supplementary [metadata](../metadata/table-metadata.md) sidecar for facts the metastore does not
track, such as ownership and last-write provenance. This split is an [architectural
invariant](../developer/architecture.md#architectural-invariants).

A DuckLake catalog's metadata being *in* PostgreSQL does not change that: those tables belong to the DuckLake
specification, live in their own database that DuckHaven's migrations do not manage, and are read directly rather than
cached, the same relationship Polaris has with its own database on the same PostgreSQL instance.

## DuckHaven-owned catalogs

When DuckHaven creates a catalog it grants its service principal the full catalog-management set
(`CATALOG_MANAGE_CONTENT`, `CATALOG_MANAGE_METADATA`, `CATALOG_MANAGE_ACCESS`) and enables drop-with-purge so that
`DROP TABLE` reclaims data files.

## Storage migration

A catalog's storage backend is chosen when it is created, and an admin can move it later, for example from the bundled
object store to an S3 bucket, with its data and snapshot history. The catalog keeps its slug, so workspaces and SQL
that use it keep working. While the move runs, the catalog is read-only: reads work, writes are refused. See
[Migrate a catalog's storage](migrate-catalog-storage.md).

## Credential vending

For an Iceberg catalog, Polaris vends short-lived, connection-scoped storage credentials when an
[agent](../compute/agents.md) attaches it. For a DuckLake catalog there is no such vendor, so DuckHaven mints the
credentials itself and sends them with the query. Either way nothing long-lived is written to an agent. See [Storage
backends](storage-backends.md) and [DuckLake](ducklake.md).

An Iceberg catalog also carries a fallback credential, minted the DuckLake way and scoped to the catalog's storage
location, for the moments when a table's vended credential is being replaced. The vended credential always takes
precedence while it exists, because it is scoped to the one table.

## Related

- [Tables & Iceberg](tables.md) — what lives inside an Iceberg catalog.
- [DuckLake](ducklake.md) — the other kind, and what it trades away.
- [Manage catalogs](manage-catalogs.md) — create, attach, detach, and drop catalogs and their schemas/tables.
