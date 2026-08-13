# Run queries

Worksheets are DuckHaven's primary surface — a browser SQL editor backed by the [agent](../concepts/agents.md) you
choose.

## Worksheets and tabs

Open multiple worksheets as tabs. Each worksheet remembers its selected agent, so you can run different work against
different compute. **Double-click a tab to rename it**, and saving a worksheet names its tab after the saved query.

## Pick an agent

The engine picker lists every connected agent with its DuckDB version, memory ceiling, and the storage backends it can
serve. An agent that lacks the extension required by the workspace's [storage backend](../concepts/storage-backends.md)
is shown disabled — picking it would fail fast.

## Run a statement

Press **Ctrl+Enter** (or the Run button) to execute the statement under the cursor — runs are statement-aware, not
whole-buffer. While a query runs you can **Cancel** it; a wall-clock timeout also applies.

## Autocomplete and IntelliSense

As you type, the editor suggests completions based on where the cursor sits in the statement. Suggestions appear
automatically; press **Ctrl+Space** (or **Cmd+Space**) to summon them on demand, and type a **`.`** after a name to
drill into it.

What it offers depends on context:

- after `FROM` / `JOIN` — the workspace's **catalogs**, **schemas**, and **tables**;
- after `catalog.` or `catalog.schema.` — that catalog's **schemas** and **tables**, fetched on demand, so you can
  reference another catalog attached to the workspace without switching your active one;
- after `schema.` — the **tables** in that schema;
- after a table or alias and a dot (e.g. `s.` for `FROM sales s`) — that table's **columns**, with their types. Aliases
  are resolved from the statement's `FROM`/`JOIN`;
- inside `SELECT`, `WHERE`, `GROUP BY`, `ORDER BY` and similar — **columns** from every table in scope, including each
  table in a `JOIN`. When more than one table is joined, each column shows the table it came from so identically-named
  columns can be told apart. Columns come first; DuckDB **functions** and **keywords** are held back until you start
  typing, so they don't bury the column list;
- after `CAST(… AS` or in a column definition — DuckDB **data types**.

Functions show their signature and, where DuckDB provides one, a usage example; typing the opening parenthesis brings up
**parameter hints**. Function, keyword, and type suggestions are read from the agent you've selected, so a connected
agent makes them richer — but keyword completion still works before an agent connects. Columns are fetched on demand the
first time you reference a table and appear as soon as they load. Running a `CREATE`, `ALTER`, or `DROP` refreshes the
catalog automatically, so a newly created or altered object is available to complete against right away — no manual
catalog refresh needed.

This release does autocomplete and signature help only; it does not flag SQL errors with red underlines.

## Browse the catalog while you write

The catalog tree beside the editor is read-only browsing — clicking a table never changes what's in your worksheet, so
exploring the catalog never risks losing what you've typed. Hover a table for a preview card (row count, size,
columns); to reference it in your query, **drag it onto the editor** — the fully-qualified `catalog.schema.table` name
drops in wherever you release it, leaving the rest of your SQL untouched.

## Read results

- Results appear in a grid below the editor, paged on demand so large results never load whole.
- Each column header shows its data type; hover a header for the full type when it's too long to fit.
- Click a column header to sort by that column. Sorting only reorders the rows already loaded — if
  not all rows have loaded yet, the row count switches to **Sorted: N of M loaded** as a reminder to
  page through the rest before treating the order as final.
- If a query fails, the full error message shows in the results pane in place of the grid.
- Export the current result to **CSV**.
- After a run, open the **Profile** tab to inspect performance — see [Read query profiles](query-profiles.md).

## Allowed SQL

Worksheets accept `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` and catalog DDL (`CREATE`, `ALTER`, `DROP`). Sandbox
escapes (`ATTACH`, `COPY`, `LOAD`, `SET`, `PRAGMA`, …) are rejected. See [SQL support](../reference/sql-support.md).

## Inspect your tables

Every catalog has a built-in, read-only `information_schema` you can query like any other table — list what's in a
catalog, then `DESCRIBE` a table to see its columns:

```sql
SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = 'analytics';
DESCRIBE analytics.analytics.events;
```

In the catalog tree it appears under each catalog as a lock-marked **`information_schema`** node badged *read-only*;
expand it to browse the supported views. It is not a stored schema — it never appears in Polaris and cannot be
written to.

See [Inspecting metadata](../reference/sql-support.md#inspecting-metadata-information_schema) for the full surface.

## Save for later

Save a frequently used query with a name and an optional default agent — press **Ctrl+S** (or the **Save…** button) to
name and save the current worksheet. See [Saved queries](saved-queries.md). Every run is recorded in the workspace
[history and audit log](../operations/monitoring.md).
