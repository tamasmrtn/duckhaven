---
type: concept
---

# System catalog

The `duckhaven` catalog lets you query DuckHaven's own access audit log, current access and query history in SQL,
read-only, from any workspace. Each table needs a permission of its own.

!!! info "Availability"
    On the single-host Compose stack, on by default; see
    [Enable the system catalog database](../deployment/system-catalog.md). On
    [Azure with Terraform](../deployment/azure-terraform.md#system-catalog), on by default. The
    [HA stack](../deployment/high-availability.md#limitations) has none. Each table needs the permission listed in
    [Who can read it](#who-can-read-it); the `admin` role holds them all.

Name its tables in full, in a worksheet or any other query:

```sql
SELECT event_time, action, actor_email, subject_email
FROM duckhaven.access.audit
WHERE category = 'access'
ORDER BY event_time DESC
LIMIT 20;
```

## What it holds

| Table | Holds | Needs |
|---|---|---|
| `duckhaven.access.audit` | Every event of the [access audit log](../access/audit-log.md) | `audit:read` |
| `duckhaven.access.grants` | Current access: one row per role, membership, catalog attachment, catalog grant, agent access mode, agent grant and workspace default compute | `audit:read` |
| `duckhaven.query.history` | Finished queries, with who ran them, where, on which agent and for how long | `queries:admin` |

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

### Find it in the catalog tree

For someone who may read any of its tables, the catalog tree lists `duckhaven` first, ahead of the workspace's
catalogs, badged *system*, with the tables they may read and their columns. It is absent for someone who may read none
of them. On the **Catalog** page, click it, one of its schemas or one of its tables
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

Each table needs one permission, and a query needs it for every table it reads:

| Tables | Permission |
|---|---|
| `duckhaven.access.audit`, `duckhaven.access.grants` | `audit:read` |
| `duckhaven.query.history` | `queries:admin` |

The `admin` role holds both; the [permissions reference](../reference/permissions.md) lists what else each allows. A
query made for someone else, by the [assistant](../ai/assistant.md) say, needs the permission for each person it runs
for. Without it the query is refused with `403` and `"error": "grant_denied"`, and a message that names each table it
may not read, such as `Reading duckhaven.query.history needs the queries:admin permission.`

The system catalog's database applies the same rule itself: whatever a statement names, it returns rows only from the
tables everyone the query runs for may read.

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
