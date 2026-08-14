# Lineage

Lineage answers two questions about a table: *where did this data come from*, and *what breaks if I change it*.
DuckHaven records both as a graph of relationships between datasets, and shows it on the Lineage tab of any table in
the catalog explorer.

Two things produce that graph. DuckHaven derives lineage from the SQL it runs, with no configuration and no
instrumentation, and it accepts lineage imported from a tool that already knows about it —
[dbt](../guides/import-dbt-lineage.md) today, others later. Both end up in the same graph, and every relationship
records which of them asserted it.

## What a relationship means

An edge runs from a **source** dataset to a **target** dataset and means *"data in the target was produced using the
source"*. That is deliberately a slightly weaker claim than "data flowed from the source into the target": at
table-level granularity DuckHaven cannot tell a column that was copied from a table that was only joined against or
filtered on, and pretending otherwise would be the kind of confident-but-wrong metadata that is worse than none.

For impact analysis — the common reason to look — the weaker claim is the useful one anyway. If a table was referenced
in building yours, changing it is your problem.

## What creates lineage

Lineage comes from statements that **write** a dataset:

| Statement | Recorded as |
|---|---|
| `CREATE TABLE … AS SELECT` | `create_table_as` |
| `CREATE VIEW … AS SELECT` | `create_view` (not yet reachable — see below) |
| `INSERT INTO … SELECT` | `insert` |
| `UPDATE …` with a source subquery | `update` |
| `MERGE INTO … USING` | `merge` |
| `DELETE FROM …` with a source subquery | `delete` |

Joins, aliases, CTEs, nested subqueries and multi-statement scripts are all handled, and each statement in a script is
paired with its own sources.

This works the same way regardless of how the SQL reached DuckHaven — an interactive worksheet, a
[scheduled run](../guides/schedule-queries.md), or an external tool connected through a
[SQL session](sql-sessions.md). Tools like dbt that run their transformations through DuckHaven therefore produce
lineage without any integration at all.

### What does not create lineage

Some omissions are deliberate, and knowing them is part of reading the graph correctly:

- **Reads.** A `SELECT` touches tables but produces no dataset, so it is not lineage. Query history already records
  who read what.
- **Writes with no source dataset.** `INSERT … VALUES` and `COPY … FROM '<file>'` have nothing upstream to point at.
  DuckHaven records nothing rather than inventing an edge.
- **`CREATE TABLE` with only a column list.** It declares a shape; it derives nothing.
- **A table built from itself.** A self-referencing edge carries no information.
- **Statements DuckHaven cannot parse.** Lineage extraction fails quietly: the query is unaffected and no edge is
  recorded. The `duckhaven_lineage_extract_failures` metric counts these, so a gap is visible rather than silent.

## Why lineage is read from the SQL

DuckHaven derives lineage by parsing the statement, not by inspecting the execution plan. The plan cannot supply it:
DuckDB names the tables a statement *reads* but never the table it *writes*, and it dissolves a view into its base
tables, so a view could never appear as a node. Parsing gives both sides, and keeps every referenced object visible as
itself.

!!! note "Views cannot be created in a DuckHaven catalog yet"
    DuckDB's Iceberg extension does not implement `CREATE VIEW` — it fails with *"Not implemented Error: Create View"* —
    so no view can exist in a catalog to have lineage. DuckHaven's extractor already handles the statement, so views
    will appear in the graph as their own nodes once the engine supports creating them.

## Where lineage lives

Edges are stored in Postgres alongside DuckHaven's other entities, and they belong to the **catalog**, not the
workspace — the same reasoning as the [table-metadata sidecar](metadata.md#the-table-metadata-sidecar). A relationship
between two tables is a fact about the data, so a catalog attached to both a `dev` and a `prod` workspace has one
lineage graph rather than two divergent copies.

The workspace is still the boundary you *read* through: traversal never leaves the catalogs your workspace attaches.

## Provenance

Every edge records:

- **Provider** — what asserted it. `execution` is lineage DuckHaven derived from SQL it ran; anything else is the name
  of an importer, such as `dbt`.
- **Operation** — the kind of statement, from the table above.
- **First seen, last seen, and an observation count** — when the relationship first appeared, when it was last
  re-asserted, and how many times.
- **The producing query**, for execution-derived edges, so you can open the exact SQL from the graph.

DuckHaven stores the *current* graph rather than an event log. It does not need one: query history already retains
every statement's SQL text and timestamps indefinitely, so the history is recoverable from the audit trail, and the
three fields above answer "when did this start, and is it still happening" without a second copy of it.

## When producers disagree

Multiple producers can describe the same pair of tables, and DuckHaven keeps all of their claims. If dbt and
execution-derived lineage both say `a → b`, the graph shows one edge listing both providers. If dbt says `a → b` while
execution says `c → b`, the graph shows **both edges**, each labelled.

That is deliberate. A disagreement is usually the most interesting thing lineage can tell you: a declared dependency
that never actually runs, or a runtime dependency nobody declared. Silently picking a winner would hide it.

For the same reason, no producer can delete another's edges. Re-importing a dbt project prunes stale **dbt** edges and
nothing else.

## Access

Lineage follows the same access rules as the rest of the catalog, with one wrinkle worth understanding: a graph names
tables, and a table name can reveal more than its rows do.

- Tables in catalogs your workspace does not attach are **not in the graph at all**.
- In a catalog attached in [scoped](permissions.md) mode, a table you hold no grant on appears as a **restricted node**:
  it keeps its position and its connections, but carries no catalog, schema, or table name.

Restricted nodes are shown rather than removed on purpose. Dropping them would silently shorten paths, making a partial
graph indistinguishable from a complete one — you would have no way to tell "nothing upstream" from "something upstream
you cannot see".

Importing lineage requires `writer` on the target's catalog, and imported names are redacted on the way out just like
any other, so importing a graph is not a way to learn names you could not otherwise see.

## Limits

!!! note "Column-level lineage is not available yet"
    Every edge carries an empty column list today. The graph is table-level only. The API field exists so that adding
    column-level lineage later does not change the contract.

Other limits worth knowing:

- **Renaming a table breaks its lineage.** The renamed table is a new asset with no history, and the old table's edges
  are removed when the old name is dropped. Re-running the transformation rebuilds them.
- **Dropping a table removes its edges**, on both sides — the same cleanup that removes its metadata sidecar and its
  grants. Recreating the table and re-running the work restores them.
- **Renaming a catalog is safe.** Assets are keyed by the catalog's identity, not its slug.
- **Graphs are bounded.** Traversal is capped at 5 hops and 500 nodes; when a cap is hit the response says so and the
  UI tells you rather than quietly showing a subset.

## Related

- [Import lineage from dbt](../guides/import-dbt-lineage.md) — the first importer.
- [Metadata](metadata.md) — what else DuckHaven records about a table.
- [Permissions](permissions.md) — the grant tiers redaction is based on.
- [Query execution](query-execution.md) — where execution-derived lineage comes from.
