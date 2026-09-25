# Catalogs

A **catalog** is a first-class, decoupled entity — a data domain with its own metadata store and
[storage backend](storage-backends.md) — attached to one or more [workspaces](workspaces.md) (a many-to-many
relationship, like Databricks' Unity Catalog).

## Catalog kinds

Every catalog has a **kind**, which says where its metadata lives and what format its tables are in. The two are one
choice, not two, because they are not independently selectable.

| Kind | Metadata store | Table format | Readable by |
|---|---|---|---|
| `iceberg_polaris` (default) | [Apache Polaris](https://polaris.apache.org/) | Apache Iceberg | Spark, Trino, Flink, PyIceberg, DuckDB |
| `ducklake` | `ducklake_*` tables in PostgreSQL | [DuckLake](ducklake.md) | DuckDB only |

Iceberg + Polaris is the default and needs no configuration. DuckLake is off unless an operator
[enables it](../deployment/ducklake.md).

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
- Each attachment has an **access mode**. By default (`open`) the workspace role governs the whole catalog; switching an
  attachment to `scoped` narrows access down to the catalog, schema, or table per principal — see
  [scoped access](permissions.md#scoped-access).

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

## The metastore owns structure; DuckHaven does not

DuckHaven never shadows catalog *structure* (schemas, tables, columns) in its own schema. The catalog's metastore is
the source of truth — Polaris for an Iceberg catalog, the `ducklake` database for a DuckLake one — and DuckHaven's own
database holds only a supplementary [metadata](metadata.md) sidecar for facts the metastore does not track, such as
ownership and last-write provenance. This split is an
[architectural invariant](architecture.md#7-architectural-invariants).

A DuckLake catalog's metadata being *in* PostgreSQL does not change that: those tables belong to the DuckLake
specification, live in their own database that DuckHaven's migrations do not manage, and are read directly rather than
cached — the same relationship the Polaris metastore has always had with the same PostgreSQL instance.

## DuckHaven-owned catalogs

When DuckHaven creates a catalog it grants its service principal the full catalog-management set
(`CATALOG_MANAGE_CONTENT`, `CATALOG_MANAGE_METADATA`, `CATALOG_MANAGE_ACCESS`) and enables drop-with-purge so that
`DROP TABLE` reclaims data files.

## Storage migration

!!! note "Iceberg catalogs only"
    Storage migration rewrites the absolute file URIs Iceberg embeds in its metadata tree. DuckLake records relative
    paths and needs a different procedure, which does not exist yet — its catalogs are not offered here.

A catalog's [storage backend](storage-backends.md) is chosen at creation but is **not permanent**. An admin can move an
Iceberg catalog to a different backend — for example off the bundled object store onto a corporate S3 bucket, or
from S3 to ADLS Gen 2 — without losing data or snapshot history.

Iceberg references every file by **absolute** URI (metadata → manifest lists → manifests → data files), so a migration
cannot be a plain object copy: DuckHaven copies each table's files to the new location, rewrites those absolute paths,
and re-registers the tables in Polaris under a fresh shadow catalog. Once every table is copied and verified, the
catalog is **atomically** re-pointed at the new backend — the user-facing slug never changes, so attached workspaces and
existing SQL keep working.

While a migration runs the catalog is **read-only**: reads continue against the old location, but writes are rejected
until cutover. The old data is retained for a configurable window afterwards so a migration can be reversed if needed.
See [Migrate a catalog's storage](../guides/migrate-catalog-storage.md) for the operator workflow.

## Credential vending

For an Iceberg catalog, Polaris vends short-lived, connection-scoped storage credentials when an [agent](agents.md)
attaches it. For a DuckLake catalog there is no such vendor, so DuckHaven mints the credentials itself and sends them
with the query. Either way nothing long-lived is written to an agent. See
[Storage backends](storage-backends.md) and [DuckLake](ducklake.md).

## Related

- [Tables & Iceberg](tables.md) — what lives inside an Iceberg catalog.
- [DuckLake](ducklake.md) — the other kind, and what it trades away.
- [Manage catalogs](../guides/manage-catalogs.md) — create, attach, detach, and drop catalogs and their schemas/tables.
