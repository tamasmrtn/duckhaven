---
type: concept
---

# System catalog

The `duckhaven` catalog lets you query, read-only in SQL, the schemas, tables and columns you can see, and DuckHaven's
audit log, access, query history and lineage. Every member can read its `info_schema`, which shows each person only what
they can see; each other table needs a permission of its own.

!!! info "Availability"
    On the single-host Compose stack, on by default; see
    [Enable the system catalog database](../deployment/system-catalog.md). On
    [Azure with Terraform](../deployment/azure-terraform.md#system-catalog), on by default. The
    [HA stack](../deployment/high-availability.md#limitations) has none. `info_schema` needs no permission; each other
    table needs the one listed in [Who can read it](#who-can-read-it), and the `admin` role holds them all.

Name its tables in full, in a worksheet or any other query:

```sql
SELECT schema_name, table_name, row_count, bytes, last_altered
FROM duckhaven.info_schema.tables
WHERE catalog_name = 'sales'
ORDER BY schema_name, table_name;
```

## What it holds

| Table | Holds | Needs |
|---|---|---|
| `duckhaven.info_schema.catalogs` | The catalogs attached to the workspace the query runs in, with their kind and access mode | Nothing: every member |
| `duckhaven.info_schema.schemata` | The schemas you can see in those catalogs | Nothing: every member |
| `duckhaven.info_schema.tables` | The tables you can see, with their format, row count, size, owner and last write | Nothing: every member |
| `duckhaven.info_schema.columns` | The columns of those tables, with the type a query sees | Nothing: every member |
| `duckhaven.access.audit` | Every event of the [access audit log](../access/audit-log.md) | `audit:read` |
| `duckhaven.access.grants` | Current access: one row per role, membership, catalog attachment, catalog grant, agent access mode, agent grant and workspace default compute | `audit:read` |
| `duckhaven.access.users` | Every user and service account, with its global role | `audit:read` |
| `duckhaven.access.workspaces` | Every workspace | `audit:read` |
| `duckhaven.access.credentials` | Every personal access token: who holds it, and until when | `audit:read` |
| `duckhaven.access.table_access` | Each table a finished query read or wrote, and who ran it | `audit:read` |
| `duckhaven.query.history` | Finished queries, with who ran them, where, on which agent, for how long and at what cost | `queries:admin` |
| `duckhaven.lineage.table_lineage` | Which tables feed which, in every catalog | `audit:read` |
| `duckhaven.lineage.column_lineage` | Which columns feed which | `audit:read` |

Every column, and how long each table keeps its rows, is in the
[system catalog reference](../reference/system-catalog.md).

## How it works

The tables are a copy, kept in a database of its own and refreshed every 10 seconds by default. An audit event reaches
the copy once every transaction that was writing before it has finished, and `access.grants` is replaced after each
access change and at least every 5 minutes. For the events of the last few seconds, use **Admin** → **Audit**; see
[Review access changes](../access/review-access-changes.md).

The catalog is not one of the workspace's catalogs. A query that names one of its tables attaches it for that query,
read-only, through a login that can read its tables and nothing else. It joins with the workspace's own catalogs like
any other:

```sql
SELECT h.finished_at, h.user_email, h.status, h.sql
FROM duckhaven.query.history AS h
WHERE h.workspace_slug = 'analytics'
  AND h.user_email IN (
    SELECT actor_email
    FROM duckhaven.access.audit
    WHERE action LIKE 'catalog_grant.%' AND event_time > now() - INTERVAL 7 DAY
  )
ORDER BY h.finished_at DESC;
```

To see who read a table in the last week, and how often:

```sql
SELECT user_email, count(*) AS queries, max(finished_at) AS last_read
FROM duckhaven.access.table_access
WHERE catalog_name = 'sales' AND schema_name = 'analytics' AND table_name = 'orders'
  AND access = 'read' AND finished_at > now() - INTERVAL 7 DAY
GROUP BY user_email
ORDER BY queries DESC;
```

`before`, `after` and `details` in `access.audit`, and `tags` in `query.history`, are JSON text. Read a field with
`->>`:

```sql
SELECT event_time, actor_email, subject_email, after ->> 'tier' AS tier
FROM duckhaven.access.audit
WHERE action = 'catalog_grant.create'
ORDER BY event_time DESC;
```

In a [SQL session](sql-sessions.md), the first statement that names it attaches it for the rest of the session, and
each later statement is checked again.

### What info_schema shows

`info_schema` lists the catalogs attached to the workspace the query runs in, and in them what you can see on the
**Catalog** page: everything in an open catalog, and in a [scoped](../access/scoped-access.md) one what your grants
cover, at any tier. A grant on a table shows the table and its schema; on a schema, the schema and its tables; on the
catalog, all of it. The `admin` role sees no more than its grants. A query made for someone else shows only what both
of you can see.

Unlike DuckDB's own [`information_schema`](../reference/sql-support.md#inspecting-metadata-information_schema), it
works in a workspace with a scoped catalog, and it lists the columns of an Iceberg table with the type `DESCRIBE`
shows:

```sql
SELECT table_name, column_name, data_type, is_nullable
FROM duckhaven.info_schema.columns
WHERE catalog_name = 'sales' AND schema_name = 'analytics'
ORDER BY table_name, ordinal_position;
```

It is read from each catalog's own metadata, so it trails changes:

- **A change made through DuckHaven** shows within about two publish intervals, 20 seconds by default. That covers a
  statement that creates, alters, writes to or drops a table or schema, and a schema or table created or dropped on the
  **Catalog** page.
- **Any other change**, such as another engine writing to the same Iceberg catalog, shows when the catalog is next read
  whole, every 15 minutes by default (`SYSTEM_CATALOG_METADATA_REFRESH_S`).
- **A newly attached catalog** is in `info_schema.catalogs` within the publish interval, and its schemas and tables
  after its first read, which comes within a few minutes.

`refreshed_at` on each row says when it was read. Views and macros are not listed.

### Find it in the catalog tree

The catalog tree lists `duckhaven` first, ahead of the workspace's catalogs, badged *system*, with the tables you may
read and their columns: `info_schema` for every member, and each other table for those who hold its permission. On the
**Catalog** page, click it, one of its schemas or one of its tables
to open its page, as for any catalog:

- **The catalog's page** counts its schemas and tables. Under **Details** it says what the catalog holds and how fresh
  it is, and lists each table you may read with the permission it needs.
- **A schema's page** lists its tables, what each holds and the permission each needs.
- **A table's page** says the permission it needs, lists its columns and previews its first 20 rows. The preview is a
  query like any other, so it needs an agent connected. **Query this table** opens a worksheet holding
  `SELECT * FROM "duckhaven"."access"."audit" LIMIT 100;` for it.

These pages are read-only. They have no way to alter, drop or grant anything, and no **History**, **Health**,
**Lineage**, **Semantics** or **Permissions** tab.

Beside the editor the tree only browses: drag a table into your SQL to insert its full name. The editor also completes
its schemas, tables and columns after `duckhaven.`.

The badge reads *unavailable* while the API cannot reach its database, and a query on it then fails. The tree's search
box does not find its tables.

### Who can read it

Every member of the workspace reads `info_schema`, which shows each of them only what they can see; see
[What info_schema shows](#what-info_schema-shows). Each other table needs one permission, and a query needs it for every
table it reads:

| Tables | Permission |
|---|---|
| `duckhaven.info_schema.catalogs`, `.schemata`, `.tables`, `.columns` | None |
| Every table in `duckhaven.access` and `duckhaven.lineage` | `audit:read` |
| `duckhaven.query.history` | `queries:admin` |

The `admin` role holds both; the [permissions reference](../reference/permissions.md) lists what else each allows. A
query made for someone else, by the [assistant](../ai/assistant.md) say, needs the permission for each person it runs
for. Without it the query is refused with `403` and `"error": "grant_denied"`, and a message that names each table it
may not read, such as `Reading duckhaven.query.history needs the queries:admin permission.`

The system catalog's database applies the same rules itself: whatever a statement names, it returns rows only from the
tables everyone the query runs for may read, and from `info_schema` only the rows they can all see.

The same check applies to opening a result. A colleague cannot open an admin's query on the system catalog from
History, and the [result cache](result-cache.md) never serves one.

## Limits

Only a `SELECT` that names its tables in full may use it, so that its rows cannot be copied into a catalog other
people can read, and so that no later statement can read it without being checked. These are refused with `403` and
`"error": "grant_denied"`, even for an admin:

- **Writing to it, or copying it**, as in `CREATE TABLE … AS` or `INSERT … SELECT`, and `EXPLAIN` of a query on it.
  Download a result to keep a copy.
- **Making it current**, with `USE duckhaven`, a `SET search_path` or `SET schema` that names it, or a query sent with
  `duckhaven` as its catalog.
- **Any other name in it**: a two-part name such as `duckhaven.audit`, another schema of its database such as
  `pg_catalog`, or `duckhaven` as an alias or column.
- **`PIVOT` in DuckDB's short form**, `PIVOT duckhaven.access.audit ON …`. The standard form,
  `SELECT … FROM duckhaven.access.audit PIVOT (…)`, works.

Each message, and what to do about it, is in [Troubleshoot sign-in and access](../access/troubleshooting.md).

Other limits:

- **When it is unavailable**, a query that reads it fails with `422` and `"error": "system_catalog_unavailable"`: it
  is turned off, or the API cannot reach its database. See
  [Enable the system catalog database](../deployment/system-catalog.md#if-it-goes-wrong).
- **An agent too old to attach it** fails the query with `422` and `"error": "agent_incompatible"`. Update the agent.
- **A workspace with a catalog of its own named `duckhaven`**, created before the name was reserved, reads that catalog
  under the name, with its own grants. Read the system catalog from another workspace.

## Related

- [System catalog reference](../reference/system-catalog.md): every table and column.
- [Access audit log](../access/audit-log.md): what is recorded, and how long it is kept.
- [Enable the system catalog database](../deployment/system-catalog.md): turning it on, and watching how far behind
  it is.
