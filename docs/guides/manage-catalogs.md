# Manage catalogs

Each [workspace](../concepts/workspaces.md) has one Apache Polaris catalog of schemas and Iceberg tables. You can manage
it from the catalog browser or with SQL — both are Polaris-backed.

## Browse

The catalog browser lists schemas and tables. Open a table to see its columns and types, owner, row count and size,
last-write provenance, and Iceberg facts (latest snapshot, whether delete files are present). You can preview sample
rows without writing a query.

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
