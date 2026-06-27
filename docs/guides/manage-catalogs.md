# Manage catalogs

A [workspace](../concepts/workspaces.md) attaches one or more [catalogs](../concepts/catalogs.md) — decoupled data
domains, each its own Apache Polaris catalog of schemas and Iceberg tables. You can manage catalogs and their contents
from the catalog browser or with SQL — both are Polaris-backed.

## Browse

The catalog browser shows a searchable tree: **workspace → catalog → schema → table** — the same tree the worksheet
sidebar uses. Each attached catalog is a top-level node (the default one is badged) carrying a small **storage-backend
icon** — a database glyph for the bundled object store, a box for AWS S3, a cloud for Azure ADLS Gen2 — so you can tell
at a glance where a catalog's data lives (hover for the full label). Expand a node to reach its schemas and
tables. Expand a table to traverse its columns and their types inline. Open a table to see the full detail: its columns
and types, owner, row count and size, last-write provenance, and Iceberg facts (latest snapshot, whether delete files
are present). You can preview sample rows without writing a query.

Three buttons sit at the top of the tree: **refresh** re-reads the catalogs from Polaris — use it after a worksheet
`CREATE SCHEMA`/`CREATE TABLE` so the new objects appear — **link** attaches an existing catalog, and **+** creates a
new catalog. **Create schema** lives on each catalog node's right-click menu.

## Catalogs

- **Create a catalog** — the **+ → Create catalog** provisions a new catalog (its own Polaris catalog + storage) and
  attaches it to the workspace. Give it a **name** — identifier-safe (lowercase letters, digits, underscores; the name
  is also the slug used in `catalog.schema.table` SQL) — and **choose its storage backend** right in the dialog: the
  bundled object store, an existing backend, or a new external S3/ADLS one. (Storage is a per-catalog choice; there is
  no separate workspace storage.)
- **Catalog information** — right-click a catalog node → *Catalog information* opens a panel with its name, Polaris
  name, storage backend (kind, name, and root URI — where the data lives), default flag, attached-workspace count, and
  creation time.
- **Attach an existing catalog** — the **link** button binds a catalog that already exists (possibly created in another
  workspace) to this one. The same catalog can be attached to several workspaces.
- **Detach** — right-click a catalog node → *Detach from workspace*. The catalog survives for any other workspace it is
  attached to; if you detach the default, another attached catalog is promoted.
- **Drop** — right-click → *Drop catalog*. Refused while the catalog is still attached to any workspace, so a shared
  catalog is never pulled out from under a peer. Drop everywhere (detach), then drop.

Catalog create/attach/detach/drop require workspace `owner`. A global drop is allowed for the catalog's creator or an
admin.

### Querying across catalogs

A worksheet has an **active catalog** (the selector in the toolbar) used for unqualified table names. Every catalog
bound to the workspace is attached when a query runs, so you can join across them with fully-qualified references:

```sql
SELECT * FROM raw.analytics.events e
JOIN curated.analytics.users u ON e.user_id = u.id;
```

Refresh also fills in row counts. A table's row count is measured by an agent and cached; tables created through the
worksheet (rather than the create-table dialog) start out with no count and show blank in the tree. Refresh probes
every table that still lacks a count and records the result, so the numbers appear after the next refresh. Tables that
already have a count are skipped, and the probe needs a connected agent — without one the tree still refreshes but the
counts stay blank.

Because Refresh skips tables that already have a count, a count can go stale after a worksheet `INSERT`. To force a
fresh measurement of one table, right-click it and choose **Recount rows** — this re-probes that table regardless of
its cached value and updates the count in place.

## Create

- **Schema** — create a namespace from the UI, or `CREATE SCHEMA` from a worksheet.
- **Table** — use the create-table dialog (specify columns and types) or `CREATE TABLE` from a worksheet.

```sql
CREATE SCHEMA analytics;
CREATE TABLE analytics.events (id INTEGER, name VARCHAR, ts TIMESTAMP);
```

`ALTER` is run as SQL through the query path; the breadth of `CREATE`/`ALTER` support depends on the DuckDB `iceberg`
extension version on the executing agent.

## Drop

- **Drop a schema** — optionally cascade to drop the tables it contains.
- **Drop a table** — `DROP TABLE` purges the underlying data files (drop-with-purge is enabled).

!!! warning "Drops purge data"
    Dropping a table reclaims its data files. There is no off-box result durability — treat drops as permanent.

## Roles

Browsing requires `reader`; creating/altering/dropping schemas and tables requires `writer` or `owner`; managing
catalogs (create/attach/detach/drop) requires `owner`. See [Permissions](../concepts/permissions.md).
