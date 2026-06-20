# Manage catalogs

Each [workspace](../concepts/workspaces.md) has one Apache Polaris catalog of schemas and Iceberg tables. You can manage
it from the catalog browser or with SQL — both are Polaris-backed.

## Browse

The catalog browser shows a searchable tree of schemas and tables — the same tree the worksheet sidebar uses. Expand a
table to traverse its columns and their types inline, without leaving the tree. Open a table to see the full detail:
its columns and types, owner, row count and size, last-write provenance, and Iceberg facts (latest snapshot, whether
delete files are present). You can preview sample rows without writing a query.

Two buttons sit at the top of the tree: **refresh** re-reads the catalog from Polaris — use it after a worksheet
`CREATE SCHEMA`/`CREATE TABLE` so the new objects appear — and **+** opens the new-schema dialog.

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

Browsing requires `reader`; creating, altering, and dropping require `writer` or `owner`. See
[Permissions](../concepts/permissions.md).
