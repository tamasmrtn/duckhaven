# Run queries

Worksheets are DuckHaven's primary surface — a browser SQL editor backed by the [agent](../concepts/agents.md) you
choose.

## Worksheets and tabs

Every tab is a **worksheet**: a private draft that DuckHaven saves to the server as you type. There is no "save before
you lose it" — close the browser, open DuckHaven on another machine, and your tabs, their SQL and their last results
are where you left them. The status beside the Run button reads *Saving…*, *Saved*, or *Offline — retrying* when the
connection drops (the edit is kept and sent once it returns).

Each worksheet keeps its own **agent**, **catalog**, **timeout** and **results**, so a tab running a long query on one
agent does not change what the tab beside it shows or runs on. New worksheets are named for when they were made;
**double-click a tab to rename it**.

Closing a tab does not delete the worksheet. The **Worksheets** view of the sidebar (switch it with the
*Worksheets | Catalog* control above the tree) lists every worksheet you have, open or closed, most recently edited
first, alongside the workspace's shared [saved queries](saved-queries.md). Click one to open it as a tab. A blank,
unnamed worksheet is simply discarded when you close it.

!!! note "Two windows on the same worksheet"
    If you edit a worksheet in two browser windows, the one that saves second is told the worksheet changed elsewhere
    and autosave pauses there. Choose **Load latest** to take the other window's version, or **Keep mine** to save
    over it.

Worksheets are private: other members of the workspace never see them. To share SQL, save it as a
[saved query](saved-queries.md).

## Pick an agent

The agent chip at the left of the toolbar says which [agent](../concepts/agents.md) the next run goes to. Open it to
choose another. Agents are listed by what a run can do with them:

- **Running** — connected and able to serve every catalog in the workspace; a run starts straight away.
- **Stopped — starts on run** — an [elastic agent](../concepts/elastic-compute.md) that is shut down. Pick it and the
  Run button reads **Start & run**: DuckHaven starts the agent and runs your query once it is up.
- **Can't serve this workspace** — up, but missing an extension a catalog or the
  [storage backend](../concepts/storage-backends.md) needs. The reason is shown and the agent cannot be picked.
- **Unavailable** — offline or failed. These are folded away behind **Show N unavailable** so a long-lived fleet does
  not bury the agents that work; a search still finds them.

Search matches agent names and hosts. If you have the *operate* tier on an elastic agent, its **⋯** menu can **Start**
it, or **Stop** it after asking for confirmation.

Each worksheet remembers the agent it last ran on. When that agent can no longer take a run, DuckHaven picks for you:
the agent you last used in this workspace, else the first healthy agent that can serve it, else a stopped elastic agent
it can start. It never picks an offline agent just because it is first in the list. If a run fails because the agent
dropped off, the error offers **Switch agent**.

The **catalog** chip beside it sets the catalog `USE`d for unqualified table names in this worksheet; it starts on the
workspace default.

## Run a statement

Press **Ctrl+Enter** (or the Run button) to execute the statement under the cursor — runs are statement-aware, not
whole-buffer. While a query runs you can **Cancel** it; a wall-clock timeout also applies — ten minutes unless you
change it for this worksheet under the toolbar's settings icon.

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

The tree starts collapsed and remembers what you expand, per workspace, in this browser; **Collapse all** (the
double-chevron in its header) closes everything again. To find something, type at least two characters in the search
box: the search runs on the server across every catalog attached to the workspace — including ones you have not
expanded — and shows each match with its catalog and schema opened out. Clear the box to return to the tree as you left
it. See [Manage catalogs](manage-catalogs.md#browse).

## Read results

- Results appear in a grid below the editor, paged on demand so large results never load whole. Rows are numbered and
  numeric columns are right-aligned, so magnitudes line up.
- Results belong to the worksheet that produced them: switching tabs shows each tab's own last run, and a new tab starts
  empty. After a reload the last run's results come back for as long as the agent keeps them (24 hours); after that the
  pane says they have expired and to run the query again.
- When you edit the SQL after running it, a *From an earlier version of this query* marker appears beside the results.
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

Worksheets are private drafts; a **saved query** is the named version the whole workspace can open and schedule. Press
**Ctrl+S** (or **Save…**) to save the worksheet as one — with a name and an optional default agent. The worksheet stays
linked to it: from then on **Ctrl+S** or **Save** updates that saved query in place, and a dot on the tab shows when
the worksheet has changes the saved query does not. See [Saved queries](saved-queries.md). Every run is recorded in the
workspace [history and audit log](../operations/monitoring.md).

## Find an earlier run

**History** lists the runs in your workspace. It opens on **your queries from the last 7 days** — where your attention
usually is. The filter row says so rather than leaving you to infer it: the user picker shows your own name and the
time control reads *Last 7 days*, and changing either widens the view. **Clear filters** returns everything to that
default. If nothing matches, the empty state names the scope that is active, so an empty table is never ambiguous
between "no history" and "narrow filter".

A run that never reached its agent — because the agent had gone offline — is listed too, as failed with *Agent not
connected*, so an error in a worksheet always has a matching entry here.

The list is paged. **Load more** appends the next page and the count tells you how many rows you are looking at —
DuckHaven does not claim a total it has not counted, because counting every row behind a filtered page is a second
pass over the whole table for a number that goes stale the moment somebody runs a query.

### Narrowing the list

- **Search** matches text anywhere in the statement, ignoring case. `%` and `_` match themselves; they are not
  wildcards.
- **Query ID** accepts a full id or just the start of one, so you can paste the shortened id the tables show. It only
  ever finds runs you could already see — knowing an id is not a way into another workspace.
- **Time** offers the last hour, 24 hours, 7 days, 30 days, all time, or a range you pick. Each preset names the date
  it resolves to, so there is no guessing whether "last 7 days" means seven calendar days or a rolling week: it is
  rolling, from the moment you opened the page.
- **Status** and **Statement type** both take several values at once.
- **Slower than** takes a number and a unit.
- **Sorting** by *Started* or *Duration* runs on the server, over the whole result set — not just the page in front of
  you.

Every one of these lives in the URL, so a narrowed view can be bookmarked or pasted to a colleague and will come back
the same. Your position within the list does not: a shared link opens on the first page.

### What "duration" means here

For a run that finished normally it is the time the agent spent executing the statement.

For a run that **failed before the agent could report one**, it is the wall-clock time between submission and failure.
This matters for the **Slower than** filter: a query that hung for two minutes and then died is exactly what you are
looking for when you hunt slow queries, and filtering on execution time alone would hide every failure.

A run that has not finished is excluded from the duration filter and sorts last under **Duration**. Its duration is
not yet known, which is not the same as zero.

### Statement types, and rows that have none

Each run is classified when it is recorded — `select`, `insert`, `create`, and so on, plus `other` for statements like
`USE` and `SET` that fit nothing more specific.

Runs recorded before DuckHaven classified statements, and statements its parser could not read, have **no type at
all**. That is deliberately different from `other`: `other` means "we understood it and nothing fits", no type means
"we do not know". Such runs stay visible in the list and disappear only when you filter by type — a filter never
claims a run is something it was never shown to be.

### Who can see what

Anyone in a workspace can narrow that workspace's history however they like, including to their own runs and to a time
window. Reading **another user's** runs, or **across workspaces**, needs the query-admin permission.

Internal machinery is never listed: table-preview queries, metadata lookups, and the
[Lakehouse-health](../concepts/maintenance.md) scanner's per-table probes. Statements run inside a
[SQL session](../concepts/sql-sessions.md) — dbt and dlt work — are real user work and do appear.
