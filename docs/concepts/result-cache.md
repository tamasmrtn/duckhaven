# Result cache

Analytical workloads repeat themselves. The same dashboard query, the same saved query on a schedule, the same question
asked again by an AI assistant: across large warehouse fleets, most queries are exact repeats of one asked minutes
earlier. When nothing the query reads has changed since, running it again only reproduces the answer DuckHaven already
has.

The **result cache** answers such a query from the earlier run's result instead. Nothing is dispatched to an agent: the
query is recorded as an ordinary run that finished at once, and its rows are read from the result that was kept. A
cached answer is never stale, because the cache asks each table's catalog whether it changed — so it stays correct
even when Spark, PyIceberg, dlt or any other engine writes to your tables behind DuckHaven's back.

The cache is **on by default**. It can be turned off for the whole deployment, per workspace, per SQL session, or for a
single run — see [Turning it off](#turning-it-off).

## When a query is answered from the cache

A run is served from the cache when all of these hold:

- **It is the same query** — the same statement as DuckDB parses it, so whitespace, comments and keyword case don't
  matter, but literals, aliases and identifiers do — run in the same workspace.
- **It would run in the same context**: the same catalog and schema for unqualified table names, the same set of
  attached catalogs, the same DuckDB [runtime](runtimes.md), and the same time zone (which decides how `TIMESTAMPTZ`
  values read).
- **Every table it reads is unchanged** since the run whose result is kept, or changed only by commits that rewrote
  files without changing the data (see [Compaction does not invalidate](#compaction-does-not-invalidate)).
- **The caller may read those tables.** The cache is shared by everyone in the workspace, but the right to read a table
  is not: the caller's [grants](permissions.md) are checked on every hit exactly as they would be for a run.
- **The kept result is still there** (see [Where results are kept](#where-results-are-kept-and-for-how-long)).

Otherwise the query simply runs, as it would without a cache. When a run that could be cached finishes, its result is
kept for the next time.

## Why a cached answer is never stale

The cache does not try to notice writes as they happen — an external engine's commit would never be noticed. Instead
it records the **version of every table** a result was computed from and asks each catalog for the current version
before every hit:

| Catalog kind | What identifies a table's version |
|---|---|
| Iceberg (Polaris) | The table's current snapshot and **schema**. An `ALTER TABLE` commits a new schema without a new snapshot, so the snapshot alone would miss it. Asked with a conditional request, so an unchanged table costs Polaris a `304 Not Modified`. |
| DuckLake | The newest catalog snapshot in which the table changed — rows inserted or deleted, inlined rows flushed, files merged, a column added or renamed. |

A DuckLake catalog can also store macros, and a macro named like a built-in function (a macro called `upper`) replaces
it for every query that runs with that catalog current. Creating, replacing or dropping a macro therefore counts as a
change for every query run with the catalog current.

A result is only kept if **nothing committed to any of its tables while the query was running**. The versions are
read before the run is dispatched and again when it finishes; if they differ, the rows might mix two versions of a
table, so they are not kept and the run is marked `changed_during_run`.

### Compaction does not invalidate

Table maintenance rewrites files without changing what a reader sees. Treating that as a change would empty the cache
every time maintenance runs, so a version change made only of such commits keeps the entry and moves it forward:

- **Iceberg**: snapshots whose operation is `replace` — compaction, rewriting data files, relocating them.
- **DuckLake**: flushing inlined rows to Parquet, merging adjacent files, and rewriting files to drop deleted rows.

!!! note "Flushing inlined deletions counts as a change"
    DuckLake records a flush of *inlined deletions* with the same marker a real `DELETE` leaves, so it cannot be told
    apart from one. Entries for that table are dropped: a miss, never a wrong answer.

## What is never cached

Some queries can never be answered from the cache, because their answer depends on more than the query and the data.
The run records why in `cache_detail`:

| `cache_detail` | Why |
|---|---|
| `not_select` | Anything other than a single read: DML, DDL, `PRAGMA`, transaction control. |
| `multiple_statements` | A script of several statements. |
| `volatile_function` | A function whose result depends on when, where or by whom it runs: `now()`, `current_date`, `random()`, `uuid()`, `current_user`, `getvariable()`, `current_setting()`, … DuckDB's own function metadata is not trusted for this: it labels some of these as stable. |
| `unknown_function` | A function DuckHaven cannot vouch for: a user-defined or catalog-qualified one. |
| `table_function` | A table function that reads outside the catalog (`read_parquet`, `iceberg_scan`) or reports engine state (`duckdb_tables()`). `range`, `generate_series` and `unnest` are fine. |
| `system_catalog` | DuckHaven's [system catalog](metadata.md) or `info_schema`, whose content changes without any table version. |
| `not_a_table` | A view, or a name that is not a table. |
| `sample`, `time_travel`, `parameter` | `USING SAMPLE` / `TABLESAMPLE`, `AT (VERSION => …)`, and prepared-statement parameters. |
| `ambiguous_name` | A two-part name `a.b` where `a` is both a catalog and a schema name. |

`LIMIT` without `ORDER BY` **is** cached: any rows it returns are a correct answer, and so are the kept ones.

Internal runs — table previews, maintenance, metadata reads — always run.

## In SQL sessions

A [SQL session](sql-sessions.md) keeps one connection for many statements, and that connection remembers things that
change what a statement means. A session statement is served from the cache only when:

- **No transaction is open.** Inside `BEGIN … COMMIT`, DuckDB keeps reading the table version it saw first, and the
  transaction sees its own uncommitted writes — neither is what the catalog reports. Statements in a transaction are
  marked `in_transaction` and always run.
- **The session has created nothing session-local.** A temporary table, view or macro can hide a catalog table of the
  same name, a `search_path` widens where names are found, and an `ATTACH` adds a catalog. After any of these the
  session stops using the cache for good (`session_state`).
- **No other statement of the session is still running** (`session_busy`), because its effect on the session is not
  known yet.

`USE` and `SET TimeZone` do *not* stop caching. After every statement the agent reports the catalog, schema and time
zone the connection is in, and the next statement is keyed in that context — exactly like a one-shot query, so a
session and the worksheet share entries for the same query.

## Where results are kept, and for how long

- **Small results** — up to `RESULT_CACHE_INLINE_MAX_BYTES`, 1 MiB by default — are copied into the control plane's
  database. Serving one needs no agent at all: it works while the agent that ran it is down, and an
  [elastic pool](elastic-compute.md) scaled to zero answers it without a cold start.
- **Larger results** stay in the result file on the agent that ran the query, which keeps the file past its normal
  24-hour retention while the entry exists. If the agent is gone or the file was removed, the next lookup notices,
  drops the entry, and the query runs.

An entry expires after `RESULT_CACHE_TTL_HOURS` (24 by default) without a hit, each hit restarting the clock, and never
lives longer than `RESULT_CACHE_MAX_AGE_HOURS` (7 days). Space for small results is bounded per workspace and in total;
when it runs out, the entries least worth keeping go first — those that took little time to compute, are rarely hit,
and are large — and an entry younger than `RESULT_CACHE_MIN_LEASE_S` is never evicted before it has had the chance to
be hit. On the agent, retained files are bounded by `RESULT_CACHE_MAX_BYTES`. See
[Configuration](../reference/configuration.md#result-cache).

## Seeing what the cache did

Every run says what the cache did with it in `cache_status` — `hit`, `miss`, `bypass` (the cache was turned off, or
could not decide within `RESULT_CACHE_LOOKUP_TIMEOUT_S`) or `ineligible` — with the reason in `cache_detail`. A hit
names the run whose result it served in `result_source_query_id`.

In the worksheet a hit carries a **Cached** badge linking to that run, and a **Re-run without cache** action. Its
profile is the source run's, marked as such — nothing ran for the hit itself. History shows the result cache in place
of an agent. A hit is still recorded in [lineage](lineage.md) and History, so who read what stays auditable.

The cache's own [metrics](../operations/monitoring.md#metric-reference) count lookups, admissions and evictions. Hits
are deliberately left out of `duckhaven_queries_total` and the query duration histogram, which describe work agents
did.

## Turning it off

| Scope | How |
|---|---|
| Deployment | `RESULT_CACHE_ENABLED=false`. Overrides every workspace. |
| Workspace | An owner unticks **Answer repeated queries from the result cache** in workspace settings, or sends `PATCH /api/workspaces/{workspace}` with `result_cache_enabled: false`. |
| Session | `use_cache: false` when opening it. |
| One run | `use_cache: false` on `POST /api/workspaces/{workspace}/queries` or on a session statement — the worksheet's **Re-run without cache**. The result still refreshes the cache. |

Turn it off when measuring query performance: a benchmark that repeats its queries would otherwise time the cache.

!!! note "Not this"
    The cache reuses a result only for the *same* query. It does not answer one query from another's result (a
    narrower filter, a coarser grouping), reuse intermediate results, or wait for an identical query that is already
    running. `AT (VERSION => …)` time travel and views are not cached yet.

## Related

- [Query execution](query-execution.md) — what happens when a query is not answered from the cache.
- [SQL sessions](sql-sessions.md) — the persistent-connection path.
- [Permissions](permissions.md) — the grants every hit is checked against.
- [Configuration](../reference/configuration.md#result-cache) — every setting named here.
