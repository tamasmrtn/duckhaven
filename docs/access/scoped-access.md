---
type: concept
---

# Scoped access

A scoped catalog shows each workspace member only the schemas and tables a grant gives them, down to a single table,
instead of applying their workspace role to everything.

In an open catalog, membership is all it takes: every member, `reader` included, can read and write every table. Scoping
a catalog is how you say "this analyst sees the `marketing` schema but not `finance`", "only these people may change
this table", or "this coding agent may discover a table but never read its rows". It is opt-in per catalog attachment,
so nothing changes for teams that do not need it. How scoping fits with the other access layers is in [How access
works](how-access-works.md).

!!! info "Availability"
    Every catalog kind and every deployment. Switching a catalog's access mode, and granting on it, needs the workspace
    `owner` role.

## Open and scoped catalogs

Each catalog attachment has an **access mode**:

- **`open`** (the default) — every member of the workspace can read and write every object, whatever their role.
- **`scoped`** — the workspace role alone is not enough; a member sees only what a **grant** gives them.

Choose the mode when you create the catalog — the **New catalog** dialog asks **Who can see its data** — or switch an
existing one from its **Permissions** dialog (right-click the catalog in the tree), or from **Admin** → **Catalog
access**, which lists every attached catalog with its mode.

Create a catalog scoped when its data is sensitive from the start. A catalog created `open` is readable by every
member at their workspace role from the moment it exists, so switching it later leaves a window. A catalog created
scoped grants its creator `writer` on the whole catalog; without that, it would be invisible even to the person who
made it, because the workspace role only caps a grant and never supplies one.

## Grants and tiers

A grant names one principal — a person or a [service account](service-accounts.md) — and one object, and covers
everything beneath that object, including tables created after the grant:

- **catalog** — every schema and table in the catalog
- **schema** — every current and future table in the schema
- **table** — one table

Each grant has a **tier**:

| Tier | Can do |
|---|---|
| `metadata` | See the object and `DESCRIBE` it to read its columns, but not read its rows |
| `reader` | Everything `metadata` can, plus read rows: query, sample, and open results |
| `writer` | Everything `reader` can, plus write and run DDL |

Two rules keep the result predictable:

- **Grants only narrow, never widen.** A member's effective tier is capped at their workspace role: a `writer` grant
  does not let a workspace `reader` write. To restrict someone to part of a catalog, grant narrowly.
- **Grants add up, and there is no deny.** Where several grants cover an object, the highest tier wins.

Manage grants from the catalog tree: right-click a catalog, schema or table, or open a table's **Permissions** tab,
and grant a principal access at that level. The same changes are scriptable, which suits access control that should be
reviewed rather than clicked:

```bash
dh grant access-mode scoped --catalog finance
dh grant set --user analyst@example.com --tier reader --schema marketing --catalog finance
dh grant list --catalog finance
```

`--user` takes an email, a name or an id. See [the CLI reference](../reference/cli.md#dh-grant).

## What is checked, and when

Every check happens in the API before a query reaches an agent, for the catalog browser, the REST API and SQL alike,
including scheduled runs.

- **Every table a query names.** A query that joins several tables is rejected unless the principal holds at least
  `reader` on each of them.
- **Ambiguous two-part names count both ways.** `sales.leads` means the `sales` schema of the active catalog, unless a
  catalog called `sales` is attached, in which case the engine reads `sales.main.leads`. The check cannot know which the
  engine will choose, so it requires access to both.
- **SQL sessions follow their context.** In a [SQL session](../querying/sql-sessions.md), an unqualified name is checked
  in the catalog and schema the session is in, which a `USE` or `SET` can change (see
  [Grants follow the session's context](../querying/sql-sessions.md#grants-follow-the-sessions-context)).
- **Views count too.** A view can read a scoped catalog the query never names. The agent then fetches that catalog
  while the query runs, and gets it only if the principal can read the whole catalog (see
  [which catalogs a query attaches](../querying/query-execution.md#which-catalogs-a-query-attaches)).
- **Denied looks like missing.** A table you hold no grant on returns `404`, not `403`, so it is indistinguishable from
  one that does not exist.

## Finding objects in a scoped catalog

Browse the catalog tree, or call the REST catalog endpoints, to see what exists, and use
`DESCRIBE ⟨catalog⟩.⟨schema⟩.⟨table⟩` for a table's columns. Both are filtered by your grants, and `DESCRIBE` needs only
the `metadata` tier. A tool can fetch all of it at once from the
[workspace metadata report](../reference/rest-api.md#workspace-metadata), which applies the same filter.
`DESCRIBE PIVOT …` is the exception: a pivot names its columns after values it reads, so it needs `reader`.

What you cannot do is ask the query engine to list objects for you. These are rejected:

- `information_schema.tables`, `information_schema.schemata` and `information_schema.columns`, and the
  `system.information_schema.…` spelling
- the `duckdb_tables()`, `duckdb_schemas()`, `duckdb_columns()` and `duckdb_views()` family, with `duckdb_indexes()`,
  `duckdb_sequences()` and `duckdb_dependencies()`
- `SHOW TABLES`, `SHOW ALL TABLES`, `PRAGMA show_tables`, `PRAGMA show_tables_expanded` and `PRAGMA database_list`
- `PRAGMA table_info(…)`, and every function that names its table only inside a string: `pragma_table_info()`,
  `pragma_show()`, `pragma_storage_info()`, and `iceberg_snapshots()` / `iceberg_metadata()`. Use `DESCRIBE` instead,
  and the table's **History** tab for its snapshots
- DuckLake's catalog methods, such as `lake.snapshots()` and `lake.table_changes(…)`, which report on a whole catalog

DuckDB computes these across every attached catalog and cannot filter their rows, so allowing them would reveal the
names of tables you were not granted. The answer has to come from the API, which can filter.

### The rejection covers the whole workspace

A query can reach every catalog the workspace binds, and
[these views span all of them](../reference/sql-support.md#inspecting-metadata-information_schema), so there is no way
to list "just the open catalog". As soon as **one** catalog in a workspace is scoped, engine-side listing is rejected
for every query in that workspace, whichever catalog is active. The error names the scoped catalog responsible.

Querying rows, `DESCRIBE` and the catalog tree keep working in the open catalogs. Only the listings above, and
[direct file reads](#files-are-read-through-tables), stop.

!!! tip "Give scoped catalogs their own workspace"
    Tools that discover tables through `information_schema`, such as dbt and most BI connectors, fail in any workspace
    that holds a scoped catalog. Attach the scoped catalog to a separate workspace instead of beside them. Column
    metadata is unaffected either way: `information_schema.columns`
    [does not work for Iceberg tables](../reference/sql-support.md#columns-and-types-use-describe) in any workspace, so
    tools that work with DuckHaven already use `DESCRIBE`.

## Files are read through tables

DuckDB can read files directly — `read_parquet('s3://…')`, `read_csv(…)`, or a quoted path in `FROM`. Such a read names
no table, so there is no grant to check, yet the files under a scoped catalog are its tables' data. So in a workspace
with a scoped catalog, queries read data only through tables. A [SQL session](../querying/sql-sessions.md) can still
read files under its own staging prefix, which is how dbt and dlt load data; the prefix is private to the session.

## Opening a result needs the same grants as running its query

A result holds the data its query read, so opening one is checked like running the query. Paging through a result or
opening its profile checks your current grants against every table the query read, whether you ran it or a colleague
did. Without them the result is refused with `403`, the results pane says you do not have access to the data the query
read, and the same applies to the query's author: revoking a grant also closes results run before the revocation.

Results of a session's staged-file reads are readable only by the session's author. A result from a session whose
current context was unknown can be read only if its SQL names its tables fully qualified. In a workspace with no scoped
catalog, any member can open any result.

## Lineage is filtered the same way

A [lineage](../metadata/lineage.md) graph names tables, and a name can reveal more than the rows do. A table you hold no
tier on keeps its place in the graph but loses its name, and the link to the query that produced it is withheld. See
[Lineage → Access](../metadata/lineage.md#access).

## Related

- [How access works](how-access-works.md) — how scoped access fits with the other layers.
- [Permissions reference](../reference/permissions.md#data-grant-tiers) — the grant tiers.
- [SQL support](../reference/sql-support.md#inspecting-metadata-information_schema) — inspecting metadata.
- [Troubleshoot sign-in and access](troubleshooting.md) — what a refused query looks like.
