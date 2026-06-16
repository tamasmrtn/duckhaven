# Your first query

With a [workspace](first-workspace.md) in place, open a worksheet and run some SQL.

## Pick an agent and run

1. Open a **Worksheet**.
2. Choose an [agent](../concepts/agents.md) from the engine picker. The picker shows each agent's DuckDB version, memory
   ceiling, and which storage backends it can serve; agents missing the workspace's required extension are disabled.
3. Type a statement and press **Ctrl+Enter** (or the Run button) to run the statement under the cursor:

   ```sql
   SELECT 42 AS answer;
   ```

4. Inspect the results grid. You can export to CSV, page through large results, or cancel a running query.

## Create a table

`CREATE TABLE` and `INSERT` run through the same worksheet, against the workspace's Iceberg catalog:

```sql
CREATE TABLE analytics.events (id INTEGER, name VARCHAR);
INSERT INTO analytics.events VALUES (1, 'signup'), (2, 'login');
SELECT * FROM analytics.events;
```

## What SQL is allowed

Worksheets accept data statements (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`) and catalog DDL (`CREATE`, `ALTER`,
`DROP`). Sandbox escapes such as `ATTACH`, `COPY`, `LOAD`, and `SET` are rejected. See
[SQL support](../reference/sql-support.md).

## Inspect the profile

After a query finishes, open the **Profile** tab to see a per-operator execution profile — rows, bytes, and timing per
step, plus flags for spills and bad estimates. See [Read query profiles](../guides/query-profiles.md).

## Next steps

- [Run queries](../guides/run-queries.md) — the full worksheet guide.
- [Snapshots & time travel](../guides/snapshots-time-travel.md) — query a table as of a past snapshot.
