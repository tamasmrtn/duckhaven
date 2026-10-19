---
type: how-to
---

# Create a DuckLake catalog

Create a DuckLake catalog, create and query its tables, and drop it when you are done with it.

A [DuckLake](ducklake.md) catalog's tables are readable by DuckDB and nothing else. If Spark, Trino or a PyIceberg
script needs to read the data, create an Iceberg catalog instead. A DuckLake catalog can be
[exported to Iceberg](ducklake.md#exporting-to-iceberg) later, which copies its data and current state but not its
snapshot history.

## Before you begin

- **You need:** the workspace `owner` role, and a connected agent whose image carries the `ducklake` and `postgres`
  extensions.
- **Enabled by:** `DUCKLAKE_ENABLED`, on by default; see [Enable DuckLake](../deployment/ducklake.md).

## Create the catalog

1. In the catalog tree, click **+** (*Create*) and choose **Create catalog**.
2. In **New catalog**, fill in:
    - **Name:** lowercase letters, digits and underscores, starting with a letter. It is the name you use in SQL
      (`raw.staging.events`), and names the PostgreSQL schema that holds the catalog's metadata (`cat_raw`).
    - **Catalog kind:** *DuckLake*. The choice appears only when DuckLake is enabled.
    - **Storage backend:** *Bundled object storage*, or any registered S3 or ADLS Gen2 backend. The backend is
      independent of the kind.
    - **Who can see its data:** open or scoped, as for any catalog; see [Scoped access](../access/scoped-access.md).
3. Create it. The catalog is attached to the current workspace.

Until an agent first attaches the catalog, its metadata schema exists but holds no tables: the DuckLake extension
creates them on first use. Browsing it before then shows nothing.

## Create and query tables

Create a schema and a table from the catalog tree, or in a worksheet:

```sql
CREATE SCHEMA raw.staging;

CREATE TABLE raw.staging.events (
    id BIGINT,
    occurred_at TIMESTAMP,
    payload VARCHAR
);

INSERT INTO raw.staging.events
SELECT i, now(), 'payload-' || i FROM range(100000) r(i);
```

A query can join DuckLake and Iceberg catalogs:

```sql
SELECT e.id, o.customer
FROM raw.staging.events e
JOIN sales.analytics.orders o ON o.order_id = e.id;
```

Change a table's columns with `ALTER TABLE`. A type change must widen the column, such as `INTEGER` to `BIGINT` or
`FLOAT` to `DOUBLE`.

```sql
ALTER TABLE raw.staging.events ADD COLUMN source VARCHAR;
ALTER TABLE raw.staging.events RENAME payload TO body;
```

Snapshots are catalog-wide, and a table's **History** tab lists the catalog snapshots in which it changed; see
[Snapshots & time travel](snapshots-time-travel.md).

## Check that it worked

```sql
SELECT count(*) FROM raw.staging.events;
```

The count is `100000`, and the catalog appears in the tree with its schema and table.

## Drop the catalog

Dropping a catalog **deletes its data**: the object-storage prefix is purged and the metadata schema dropped, with no
undo.

1. Detach the catalog from every workspace that attaches it: right-click it and choose **Detach from workspace**.
2. Drop it: right-click and choose **Drop catalog**, or run `dh api DELETE /catalogs/⟨catalog id⟩`.

A catalog still attached somewhere is refused with `Catalog 'raw' is still attached to 1 workspace(s). Detach it
everywhere before dropping.`

## If it goes wrong

### "No compatible agent is connected"

No agent you can use has the `ducklake` and `postgres_scanner` extensions. Check the agent's **Overview** tab under
**Compute**, and update an older agent image.

### A statement is refused for naming `__ducklake_metadata_`

DuckLake's internal catalog tables cannot be queried through DuckHaven: writing them by hand bypasses the format's
transaction protocol and corrupts the catalog. Use the catalog browser, or query the tables themselves.

### "Failed to convert DuckDB type to DuckLake - unsupported type …"

Fixed-size arrays (`INTEGER[3]`), `ENUM`, `UNION`, `VARINT` and `BITSTRING` have no DuckLake representation. Use another
type, such as a list (`INTEGER[]`) or `VARCHAR`, or put the table in an Iceberg catalog.

### "Cannot change type of column … - only widening type promotions are allowed"

`ALTER … SET TYPE` can only widen a column. To narrow one, create a new table with the type you want and copy the
rows.

### The catalog shows no schemas

No agent has attached it so far. Run any query against it.

### You want to convert an existing Iceberg catalog

That is not possible. Create the DuckLake catalog and copy each table with `CREATE TABLE … AS SELECT`, which rewrites
the data and starts a new history; see
[Converting an existing Iceberg catalog](ducklake.md#converting-an-existing-iceberg-catalog).

## Related

- [DuckLake](ducklake.md) — the concept and its limits.
- [Enable DuckLake](../deployment/ducklake.md) — operator setup.
- [Manage catalogs](manage-catalogs.md) — the same operations for either kind.
