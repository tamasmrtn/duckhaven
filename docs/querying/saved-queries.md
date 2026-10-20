---
type: how-to
---

# Saved queries

Save a worksheet's SQL under a name so everyone in the workspace can open it, run it and schedule it.

A worksheet is a private draft; a **saved query** is the shared version you publish from it. It stores SQL, not
results: each run executes the SQL again, unless the [result cache](result-cache.md) can answer it.

## Before you begin

- **You need:** workspace `writer` or `owner` to save, rename or delete a saved query. Any member can open and run one.
- **Applies to:** every deployment.

## Save a query

1. In a worksheet, press **Ctrl+S**, or click **Save…**.
2. Enter a name. Names are unique in the workspace, ignoring case: `Daily report` and `daily report` are the same query.
3. Optionally choose a **default agent**, which a [schedule](schedule-queries.md) without an agent of its own runs on.
   It starts on the worksheet's agent; choose *None — schedules pick one* to leave it to the schedule.
4. Click **Save**. If the name is taken, DuckHaven says so and the button becomes **Replace**: click it only if you mean
   to overwrite that query, which others and their schedules may rely on.

Saving links the worksheet to the saved query, and its tab shows a link icon.

## Keep editing a saved query

In a linked worksheet:

- **Ctrl+S** or **Save** writes the worksheet's SQL to the saved query, without a dialog.
- An orange dot on the tab means the worksheet has changes the saved query does not.
- **Save as…**, in the menu beside **Save**, saves a copy under a new name and links the worksheet to the copy.
- **Revert to saved** replaces the worksheet's SQL with the saved query's.

Your edits autosave privately as you type, and reach the saved query only when you save.

## Open and run a saved query

Click **Open** on a saved query, in the **Saved queries** page, the worksheet sidebar's **Worksheets** view, or the
⌘K palette. It opens in a worksheet named after it, on its default agent. If you already have a worksheet linked to it,
that worksheet is focused, with its edits, instead of a second copy. Running it updates the query's **last run**.

## Find, rename and delete

The **Saved queries** page searches names and SQL, and sorts by last update, name or last run. Each card shows who
saved the query, who changed it last and when, and when it last ran.

Use the pencil to rename a query and the bin to delete it. A rename cannot take a name another query uses. Deleting a
saved query keeps the worksheets linked to it, as ordinary drafts with their SQL.

## Check that it worked

The query appears on the **Saved queries** page with your name, and a colleague in the workspace can open it.

## If it goes wrong

### Save, rename and delete are refused

You hold the `reader` role. Ask a workspace owner for `writer`.

### "A saved query named '⟨name⟩' already exists in this workspace"

Another query has that name, ignoring case. Choose another name, or click **Replace** to overwrite it.

### A schedule fails after someone edited the query

A [scheduled run](schedule-queries.md#whose-data-grants-a-run-uses) uses the data grants of whoever last changed the
SQL. If that person has narrower grants, have someone with the right grants save the query again.

## Related

- [Run queries](run-queries.md) — worksheets.
- [Schedule queries](schedule-queries.md) — run a saved query on a cadence.
- [REST API → Saved queries](../reference/rest-api.md#saved-queries) — saving from a script.
