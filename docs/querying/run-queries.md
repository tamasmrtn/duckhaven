---
type: how-to
---

# Run queries

Write SQL in a worksheet, run it on an agent you choose, read the result, and find earlier runs in History.

A worksheet is DuckHaven's SQL editor in the browser. Each tab is a private draft, saved to the server as you type, with
its own agent, catalog and last result.

## Before you begin

- **You need:** membership of the workspace, with any role, and an agent you may use.
- **Applies to:** every deployment. The [Quickstart](../getting-started/quickstart.md) walks through a first query on
  a new install.

## Open a worksheet

1. Open **Worksheets** in the left navigation. Your open tabs are where you left them, on any machine.
2. Click **+** (*New worksheet*) for a new tab, or reopen an earlier worksheet from the **Worksheets** view of the
   sidebar (switch with **Worksheets | Catalog** at the left of the tab row). It lists every worksheet you have, open or
   closed, last edited first, and the workspace's [saved queries](saved-queries.md).
3. Double-click a tab to rename it.

The status beside **Run** reads *Saving…*, *Saved*, or *Offline — retrying*; an edit made offline is sent when the
connection returns. Closing a tab keeps the worksheet, except a blank, unnamed one, which is discarded. Worksheets are
private; to share SQL, [save it as a saved query](saved-queries.md).

## Pick an agent and a catalog

The agent chip at the left of the toolbar shows where the next run goes. Open it to choose another:

| Shown as | Meaning |
|---|---|
| **Running** | Connected and able to serve every catalog in the workspace; a run starts at once |
| **Stopped — starts on run** | A stopped [elastic agent](../compute/elastic-compute.md). **Run** becomes **Start & run**: DuckHaven starts it, then runs your query |
| **Can't serve this workspace** | Up, but missing an extension a catalog needs, or on a [runtime](../compute/runtimes.md) DuckHaven does not send work to. The reason is shown |
| **Unavailable** | Offline or failed, folded under **Show N unavailable** |

Each agent shows its runtime and DuckDB version, such as *DuckDB 1.5 · v1.5.5*, and a **Beta** mark on a beta runtime.
With the `operate` tier on an elastic agent, its **⋯** menu can start or stop it.

A worksheet keeps the agent it last ran on. When that agent cannot take a run, DuckHaven picks one: the agent you last
used in the workspace, else the first healthy agent that can serve it, else a stopped elastic agent it can start. It
prefers the default runtime and never picks a beta one; you choose a beta agent yourself.

The **catalog** chip beside it sets the catalog that names without a catalog resolve in. It starts on the workspace's
default.

## Run a statement

1. Type SQL, or drag a table from the catalog tree into the editor to insert its full `catalog.schema.table` name.
2. Put the cursor in the statement and press **Ctrl+Enter**, or click **Run**. Only the statement under the cursor
   runs.
3. To stop it, click **Cancel**. A run stops on its own after the worksheet's timeout, ten minutes unless you change it
   under the toolbar's settings icon.

What SQL you can run is in [SQL support](../reference/sql-support.md).

### Autocomplete

Suggestions appear as you type; press **Ctrl+Space** (**Cmd+Space** on a Mac) to show them, and type `.` after a name
to look inside it.

- After `FROM` or `JOIN`: catalogs, schemas and tables.
- After `catalog.` or `catalog.schema.`: that catalog's schemas and tables, so you can use another catalog without
  switching.
- After a table or alias and a dot (`s.` for `FROM sales s`): that table's columns and types.
- In `SELECT`, `WHERE`, `GROUP BY`, `ORDER BY` and similar: the columns of every table in scope, each marked with its
  table when several are joined. Functions and keywords appear once you start typing.
- After `CAST(… AS`, or in a column definition: DuckDB types.

Functions show their signature and an example, and typing `(` shows parameter hints. Functions, keywords and types come
from the selected agent's DuckDB version, so a newer runtime's functions appear once you switch to it. Running a
`CREATE`, `ALTER` or `DROP` refreshes the catalog, so a new object completes at once. The editor does not underline SQL
errors.

### Browse the catalog while you write

The catalog tree beside the editor only browses; clicking a table never changes your SQL. Hover a table for its row
count, size and columns. Type two characters in its search box to search every catalog the workspace attaches, on the
server, with each match opened out. See [Manage catalogs](../data/manage-catalogs.md#browse).

## Read the result

- The result appears below the editor, loaded a page at a time, with each column's type in its header.
- Click a header to sort. Sorting reorders only the rows loaded so far; until all are loaded the count reads
  **Sorted: N of M loaded**.
- Each tab shows its own last result. After a reload it comes back for 24 hours, while the agent keeps it.
- *From an earlier version of this query* means you have edited the SQL since it ran.
- **Copy** and **Download CSV** take the rows loaded so far. For a whole large result, read it as Parquet or Arrow
  through the [REST API](../reference/rest-api.md#whole-results-as-arrow-or-parquet).
- The **Profile** tab shows how the query ran; see [Read query profiles](query-profiles.md).
- A result answered from the [result cache](result-cache.md) shows a **Cached** badge and a **Re-run without cache**
  action.

To list what a catalog holds, or a table's columns, query them:

```sql
SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = 'sales';
DESCRIBE sales.analytics.orders;
```

`information_schema` appears in the catalog tree under each catalog, marked *read-only*. In a workspace with a
[scoped catalog](../access/scoped-access.md) it is refused, and `DESCRIBE` works for tables you can read; see
[Inspecting metadata](../reference/sql-support.md#inspecting-metadata-information_schema).

## Save for later

Press **Ctrl+S**, or **Save…**, to save the worksheet as a [saved query](saved-queries.md): a named query the whole
workspace can open and [schedule](schedule-queries.md).

## Find an earlier run

**History**, in the left navigation, lists the runs in the workspace. It opens on **your runs from the last 7 days**;
the user picker shows your name and the time control reads *Last 7 days*. Change either to widen the view, or click
**Clear filters** to return to it. The list is paged: **Load more** adds the next page. There is no total count.

Click a run to open its profile and full SQL. Every filter is kept in the URL, so you can bookmark a view or send it to
a colleague; it opens on the first page.

| Filter | What it does |
|---|---|
| **Search** | Text anywhere in the SQL, ignoring case. `%` and `_` match themselves |
| **Query ID** | A full id or its first characters, such as the short id the table shows |
| **Time** | Last hour, 24 hours, 7 days, 30 days, all time, or a range. Presets roll from when you opened the page and show the date they start |
| **Status**, **Statement type** | Several values at once |
| **Slower than** | A number and a unit |
| **Origin** | **Interactive**, **Scheduled** or **Connection** (a [SQL session](sql-sessions.md)) |
| **Sort** | By *Started* or *Duration*, over every matching run |

- **Duration** is the time the agent spent running the statement. For a run that failed before the agent reported one,
  it is the time from submission to failure, so a query that hung and died counts as slow. A run still going has no
  duration, and sorts last.
- **Statement type** is set when the run is recorded: `select`, `insert`, `create` and so on, or `other` for
  statements like `USE`. A run whose SQL did not parse has no type, and is hidden only when you filter by type.
- **Client** appears once a run from a tool is in view: `dbt-duckhaven`, `dlt-duckhaven`, `duckhaven-cli`, or
  `duckhaven-assistant` and `duckhaven-mcp` for the [assistant](../ai/assistant.md) and the
  [MCP server](../ai/mcp-server.md). **Tags** a tool attached appear beneath the SQL.

Every member sees every member's runs in the workspace. Filtering to **another person's** runs, or reading **across
workspaces**, needs `queries:admin`. Table previews, metadata lookups and the [maintenance](../data/maintenance.md)
scanner's probes are never listed.

## If it goes wrong

Messages are listed in [Troubleshoot queries](troubleshooting.md). The common ones:

- **The query fails at once with a 422.** The statement is not allowed; the message names it. See
  [SQL support](../reference/sql-support.md).
- **"Agent not connected", with Switch agent.** The agent went offline. Click **Switch agent**, or pick another in the
  agent chip. The run is still listed in History, as failed.
- **"These results have expired: agents keep them for 24 hours. Run the query again."** Run it again.
- **"The agent that ran this query is offline, so its results can't be read until it reconnects."** Wait for the agent,
  or run the query on another.
- **"You don't have access to the data this query read, so its results can't be shown."** A
  [scoped catalog](../access/scoped-access.md#opening-a-result-needs-the-same-grants-as-running-its-query) refuses the
  result to you; ask for a grant on the tables it read.
- **The worksheet says it changed elsewhere.** You edited it in another window. Choose **Load latest** to take that
  version, or **Keep mine** to save over it.

## Related

- [Saved queries](saved-queries.md) — share SQL with the workspace.
- [Query execution](query-execution.md) — what happens after **Run**.
- [Read query profiles](query-profiles.md) — why a query was slow.
- [Troubleshoot queries](troubleshooting.md) — every message, and what to do.
