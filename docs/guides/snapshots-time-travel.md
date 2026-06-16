# Snapshots & time travel

Every Iceberg [table](../concepts/tables.md) keeps a snapshot history. DuckHaven lets you browse it and query a table as
it existed at a past snapshot.

## Browse snapshot history

Open a table in the catalog browser and switch to the **History** tab. DuckHaven reads the snapshot list live from
Polaris (it is never persisted) and shows, per snapshot:

- the snapshot id and its parent,
- the committed timestamp and operation,
- and metrics — added/deleted/total records and data-file counts.

## Query at a snapshot

From a snapshot, choose **Query at this snapshot** to open a worksheet pinned to it using DuckDB's time-travel clause:

```sql
SELECT * FROM analytics.events AT (VERSION => 7287998166701990000);
-- or by time:
SELECT * FROM analytics.events AT (TIMESTAMP => '2026-05-01 00:00:00');
```

The read is pinned to that snapshot; the UI describes it as querying the table "as of" a point in time.

!!! note "Read-only — no expiration"
    Time travel is read-only. DuckHaven does **not** currently expire, roll back, or compact snapshots — snapshot
    cleanup is a roadmap item, not a shipped feature.

## Related

- [Tables & Iceberg](../concepts/tables.md) — how snapshots are produced.
