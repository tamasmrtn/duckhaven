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

- after `FROM` / `JOIN` — the workspace's **schemas** and **tables**;
- after `schema.` — the **tables** in that schema;
- after a table or alias and a dot (e.g. `s.` for `FROM sales s`) — that table's **columns**, with their types. Aliases
  are resolved from the statement's `FROM`/`JOIN`;
- inside `SELECT`, `WHERE`, `GROUP BY`, `ORDER BY` and similar — **columns** from the tables in scope, plus DuckDB
  **functions** and **keywords**;
- after `CAST(… AS` or in a column definition — DuckDB **data types**.

Functions show their signature and, where DuckDB provides one, a usage example; typing the opening parenthesis brings up
**parameter hints**. Function, keyword, and type suggestions are read from the agent you've selected, so a connected
agent makes them richer — but keyword completion still works before an agent connects. Columns appear once their table's
details have loaded (expanding it in the catalog, or referencing it in a query, fetches them).

This release does autocomplete and signature help only; it does not flag SQL errors with red underlines.

## Read results

- Results appear in a grid below the editor, paged on demand so large results never load whole.
- Export the current result to **CSV**.
- After a run, open the **Profile** tab to inspect performance — see [Read query profiles](query-profiles.md).

## Allowed SQL

Worksheets accept `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` and catalog DDL (`CREATE`, `ALTER`, `DROP`). Sandbox
escapes (`ATTACH`, `COPY`, `LOAD`, `SET`, `PRAGMA`, …) are rejected. See [SQL support](../reference/sql-support.md).

## Save for later

Save a frequently used query with a name and an optional default agent — press **Ctrl+S** (or the **Save…** button) to
name and save the current worksheet. See [Saved queries](saved-queries.md). Every run is recorded in the workspace
[history and audit log](../operations/monitoring.md).
