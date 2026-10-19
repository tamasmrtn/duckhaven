---
type: how-to
---

# Snapshots & time travel

Browse a table's snapshot history and query the table as it was at an earlier snapshot or time.

Every write to a table creates a snapshot. Time travel reads a table as of one of them; it never changes the table.

## Before you begin

- **You need:** read access to the table: any workspace role in an open catalog, or a `reader` grant in a
  [scoped one](../access/scoped-access.md).
- **Applies to:** Iceberg and DuckLake catalogs. An Iceberg snapshot belongs to one table; a DuckLake snapshot is a
  commit to the whole catalog, numbered in one sequence (see [DuckLake](ducklake.md#snapshots-are-catalog-wide)).

## Browse the history

Open a table in the catalog browser and choose the **History** tab. It lists each snapshot: its id and its
parent, when it was committed and by which operation, and the records and data files added and deleted. For a DuckLake
table, it lists the catalog snapshots in which that table changed. The history is read live from the catalog each time.

## Query a past snapshot

1. On a snapshot in **History**, choose **Query at this snapshot**. A worksheet opens with the time-travel query.
2. Run it. To read the table as of a time instead, change `VERSION` to `TIMESTAMP`:

```sql
SELECT * FROM sales.crm.customers AT (VERSION => 2656692730316015973);
SELECT * FROM sales.crm.customers AT (TIMESTAMP => TIMESTAMP '2026-10-19 18:35:40');
```

A `TIMESTAMP` reads the latest snapshot committed at or before that moment. A DuckLake `VERSION` is the catalog's
snapshot number, such as `AT (VERSION => 42)`.

To list snapshots in SQL, use `iceberg_snapshots('sales.crm.customers')` for an Iceberg table, or the catalog's
`snapshots()` method, such as `sales.snapshots()`, for a DuckLake catalog.

## Check that it worked

The query returns the rows as they were. Compare a count at the snapshot with the current count:

```sql
SELECT
    (SELECT count(*) FROM sales.crm.customers AT (VERSION => 2656692730316015973)) AS then_rows,
    (SELECT count(*) FROM sales.crm.customers) AS now_rows;
```

## If it goes wrong

### The table has no older snapshots

Old snapshots are removed when they expire. For a DuckLake catalog, the
[maintenance advisor](maintenance.md) expires snapshots past the retention target when someone applies it; for an
Iceberg catalog, an external engine does. Time travel reaches only the snapshots that remain.

### `iceberg_snapshots()` or `snapshots()` is refused

The workspace has a [scoped catalog](../access/scoped-access.md#finding-objects-in-a-scoped-catalog), and these
functions name their table in a string, which grants cannot be checked against. Use the **History** tab.

### Restoring a table to an old snapshot

DuckHaven does not roll a table back. Copy the old rows into a table with `CREATE TABLE … AS SELECT … AT (VERSION =>
…)`, then swap it in.

## Related

- [Tables & Iceberg](tables.md) — how snapshots are produced.
- [DuckLake](ducklake.md#snapshots-are-catalog-wide) — catalog-wide snapshots.
- [SQL support](../reference/sql-support.md#time-travel-syntax) — the `AT` clause.
