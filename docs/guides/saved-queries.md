# Saved queries

Save a query so your team can open it, run it, and [schedule](schedule-queries.md) it.

A **saved query** is shared with everyone in the [workspace](../concepts/workspaces.md). The tabs you type in are
[worksheets](run-queries.md#worksheets-and-tabs) — private drafts that save themselves — and a saved query is what you
publish from one.

## Save a query

From a worksheet, press **Ctrl+S** (or click **Save…**) and give the query a name. You can also pick a **default
agent**: a [schedule](schedule-queries.md) without an agent of its own runs on it. It starts on the agent the worksheet
is using; choose *None* to let schedules pick.

Names are unique within a workspace, ignoring case — `Daily report` and `daily report` are the same query. If the name
is already taken, DuckHaven says so and asks before **replacing** that query, because others (and their schedules) may
rely on it.

## Keep editing a saved query

Saving links the worksheet to the saved query, and the tab shows a link icon. From then on:

- **Ctrl+S** or **Save** writes the worksheet's SQL to the saved query straight away — no dialog.
- An orange dot on the tab means the worksheet has changes the saved query does not have yet.
- **Save as…** (in the menu beside Save) saves a copy under a new name and links the worksheet to the copy.
- **Revert to saved** replaces the worksheet's SQL with the saved query's.

Your worksheet edits are autosaved privately as you type; nothing reaches the shared query until you save.

## Open and run

Click **Open** on a saved query — here, in the worksheet sidebar's **Worksheets** view, or from the ⌘K palette — to
open it in a worksheet named after it, on its default agent. If you already have a worksheet linked to that query, it
is focused instead, with whatever edits it holds, rather than a second copy being opened. Running it updates the
query's **last run** time.

## Find, rename and delete

The **Saved queries** page searches names and SQL and sorts by last update, name, or last run. Each card shows who
saved the query, when it last changed and — if it was someone else — who changed it, and when it last ran.

Use the rename (pencil) and delete (trash) actions on each card. Any workspace member with **writer** access or above
can rename or delete any saved query in the workspace. A rename cannot take a name another query already uses.
Deleting a saved query leaves the worksheets linked to it as ordinary drafts, SQL intact.

## Notes

- There is no version history: replacing or saving over a query overwrites its SQL.
- A [scheduled run](schedule-queries.md#whose-data-grants-a-run-uses) uses the data grants of whoever **last changed
  the SQL**, not whoever first saved it.
- Saved queries store SQL only; results are produced fresh on each run (see
  [Query execution](../concepts/query-execution.md)).

## Related

- [Run queries](run-queries.md) — the worksheet basics.
- [Schedule queries](schedule-queries.md) — run a saved query on a cadence.
