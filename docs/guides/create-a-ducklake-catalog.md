# Create a DuckLake catalog

A walkthrough of creating, using and dropping a [DuckLake](../concepts/ducklake.md) catalog. It assumes DuckLake is
[enabled](../deployment/ducklake.md) and at least one agent is running an image that carries the `ducklake` and
`postgres` extensions.

## Before you start

A DuckLake catalog's tables are readable by DuckDB and nothing else. If anything outside DuckHaven needs to read this
data — Spark, Trino, a PyIceberg script — create an Iceberg catalog instead. The choice cannot be changed later without
copying the data.

## Create it

**Catalog → + → New catalog.**

1. **Catalog kind** — pick *DuckLake*. This step only appears when DuckLake is enabled; a deployment with one kind
   shows no choice.
2. **Name** — lowercase letters, digits and underscores, starting with a letter. This becomes the name you use in
   SQL (`raw.analytics.events`) and the PostgreSQL schema that holds the catalog's metadata (`cat_raw`).
3. **Storage backend** — bundled object storage, or any registered S3 / ADLS Gen 2 backend. This is independent of the
   kind.
4. **Access mode** — `open` or `scoped`, exactly as for an Iceberg catalog.

The catalog is created and attached to the current workspace.

!!! note "It starts empty and un-materialised"
    Until an agent first attaches the catalog, its metadata schema exists but holds no tables — the DuckLake extension
    creates them on first use. Browsing it before then correctly shows nothing rather than an error.

## Use it

Nothing about querying differs. Create a schema and a table from the catalog tree, or in a worksheet:

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

A workspace can attach catalogs of both kinds, and a query can join across them:

```sql
SELECT o.id, c.name
FROM   raw.staging.events o
JOIN   curated.analytics.customers c ON c.id = o.id;
```

## Time travel

DuckLake snapshots are catalog-wide, so a table's Snapshots tab lists the catalog snapshots in which that table
changed. "Query at this snapshot" generates:

```sql
SELECT * FROM raw.staging.events AT (VERSION => 42);
SELECT * FROM raw.staging.events AT (TIMESTAMP => TIMESTAMP '2026-09-01 00:00:00');
```

## Evolve a table

Add, drop and rename columns, and widen a column's type:

```sql
ALTER TABLE raw.staging.events ADD COLUMN source VARCHAR;
ALTER TABLE raw.staging.events RENAME payload TO body;
ALTER TABLE raw.staging.events ALTER id SET TYPE BIGINT;
```

Only lossless type promotions are allowed — `INTEGER` to `BIGINT`, `FLOAT` to `DOUBLE`. Narrowing a column fails.

## Drop it

Detach the catalog from every workspace, then drop it from **Catalog → catalog → Drop**, or:

```bash
dh api DELETE /catalogs/<catalog-id>
```

Dropping a catalog **deletes its data**: the object-storage prefix is purged and the metadata schema is dropped. There
is no undo.

## Troubleshooting

**"No compatible agent is connected"** — the agents you can use do not advertise the `ducklake` and `postgres_scanner`
extensions. Check Admin → Compute; an older agent image needs updating.

**A statement is rejected mentioning `__ducklake_metadata_`** — DuckLake's internal catalog tables are deliberately not
queryable through DuckHaven. Writing them by hand bypasses the format's transaction protocol and corrupts the table.
Use the catalog browser, or query the tables themselves.

**"DuckLake cannot represent column type(s)"** — `ARRAY`, `ENUM`, `UNION`, `VARINT` and `BITSTRING` have no DuckLake
representation. Use a supported type, or put the table in an Iceberg catalog.

**A catalog shows no schemas** — it has never been attached by an agent. Run any query against it.

## Related

- [DuckLake](../concepts/ducklake.md) — the concept and its limits.
- [Enable DuckLake](../deployment/ducklake.md) — operator setup.
- [Manage catalogs](manage-catalogs.md) — the same operations for either kind.
