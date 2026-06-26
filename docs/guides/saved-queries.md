# Saved queries

Save a frequently used query so your team can recall and run it later.

## Save a query

From a worksheet, press **Ctrl+S** (or click **Save…**) to save the current SQL with a name and an optional
**default agent**. Saved queries are scoped to the [workspace](../concepts/workspaces.md) and shared with its members.
The list shows who saved each query and, once it has been run, when it last ran.

## Load and run

Click **Open** on a saved query to load it into a worksheet, then run it like any other query. The saved **default
agent** is pre-selected, and running it updates the query's **last run** time.

## Rename and delete

Use the rename (pencil) and delete (trash) actions on each saved query. Any workspace member with **writer** access or
above can rename or delete any saved query in the workspace.

## Notes

- Saving with an existing name **overwrites** it — there is no version history.
- Saved queries store SQL only; results are produced fresh on each run (see
  [Query execution](../concepts/query-execution.md)).

## Related

- [Run queries](run-queries.md) — the worksheet basics.
