# Reconstruct lineage from query history

[Lineage](../concepts/lineage.md) fills in as DuckHaven watches queries run — which means that on the day you enable it,
the graph is empty. That is a bad first impression of a feature that is otherwise working perfectly: you open the
Lineage tab, see nothing, and conclude it does not do anything.

It does not have to start empty. DuckHaven keeps the full SQL text of every statement it has ever run, indefinitely, so
the graph for work you did months ago is already recoverable. A backfill replays that history through the same
extraction DuckHaven uses live, and the relationships appear as though they had been recorded at the time.

A team six months into using DuckHaven gets six months of lineage in one pass.

## What you need

**Owner** on the workspace. A backfill reads every statement the workspace has run and writes lineage across every
catalog it attaches, which is the same reach as retiring a lineage producer — so it takes the same permission.

## Rehearse it first

Start with a dry run. It derives everything exactly as a real pass would and then discards the writes, so the counts are
real but the graph is untouched:

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}' \
  https://duckhaven.internal/api/workspaces/<workspace>/lineage/backfill
```

The request returns immediately — it queues the work rather than doing it, so a long history cannot hold the request
open. Poll the same path to watch it:

```sh
curl -fsS -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  https://duckhaven.internal/api/workspaces/<workspace>/lineage/backfill
```

```json
{
  "status": "completed",
  "dry_run": true,
  "queries_scanned": 18402,
  "queries_with_lineage": 1244,
  "queries_skipped": 17103,
  "parse_failures": 55,
  "queries_failed": 0,
  "edges_created": 310,
  "edges_updated": 934
}
```

Reading those numbers:

| Counter | What it means |
|---|---|
| `queries_scanned` | Completed statements the walk read. |
| `queries_with_lineage` | Statements that established at least one relationship. |
| `queries_skipped` | Parsed fine and established nothing — reads, `INSERT … VALUES`, bare `CREATE TABLE`. Usually the large majority. |
| `parse_failures` | Statements DuckHaven could not parse. A handful is normal; a large number is worth reporting. |
| `queries_failed` | Something went wrong reading a statement. Should be zero. |
| `edges_created` / `edges_updated` | New relationships, and existing ones the walk added an observation to. |

A dry run deliberately does **not** record what it read, so the real pass afterwards covers the same range rather than
skipping it.

## Run it

Drop `dry_run` and the same request does it for real:

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  -H "Content-Type: application/json" \
  -d '{}' \
  https://duckhaven.internal/api/workspaces/<workspace>/lineage/backfill
```

By default it reaches back through all available history. To bound it, pass the oldest point you care about:

```json
{ "since": "2026-01-01T00:00:00Z" }
```

A bounded pass can be widened later — asking for more history reads only the part not already covered.

To stop one that is running:

```sh
curl -fsS -X DELETE -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  https://duckhaven.internal/api/workspaces/<workspace>/lineage/backfill
```

The stop is cooperative: the walk finishes the batch it is on and then stops. Everything it derived up to that point
stays — it is real lineage, derived the same way as everything else.

## Things worth knowing

**Backfilled relationships are not fresh.** Each statement is replayed with the time it actually ran, so a
transformation last executed in February appears in the graph as observed in February — and is therefore marked
[stale](../concepts/lineage.md#freshness) straight away. That is correct: nothing has confirmed it since. A backfill
tells you what your warehouse looked like, not that it still looks that way.

**Running it twice does nothing the second time.** DuckHaven records which range of history it has read. A repeat
request completes immediately having scanned nothing, and no relationship gains a spurious observation. It is safe to
put in a deployment script.

**One bad statement does not stop the walk.** Historical SQL is a mixed bag — dialect drift, control commands recorded
for audit, statements written against catalogs that no longer exist. Each one that fails is counted and skipped.

**It runs in the background, off the query path.** The walk proceeds one batch at a time between other work, so it
cannot slow down or fail the queries it is reading about. On a very large history, expect it to take a while rather than
to spike load; `LINEAGE_BACKFILL_BATCH_SIZE` is the knob if you want it faster or gentler.

**Only completed statements count.** A run that failed or was cancelled may have written nothing, so asserting a
relationship from it would be a guess.

**Tables that have since been dropped come back as nodes** if a historical statement named them. The relationships were
real when they happened. Dropping the table again through DuckHaven removes them.

## Configuration

| Setting | Default | What it does |
|---|---|---|
| `LINEAGE_BACKFILL_ENABLED` | `true` | Whether this replica runs the background walker. Leader-elected, so it is safe on every replica. |
| `LINEAGE_BACKFILL_TICK_S` | `15` | How often the walker wakes to advance an outstanding backfill. |
| `LINEAGE_BACKFILL_BATCH_SIZE` | `500` | How many statements one pass reads. Trades speed against load. |

See [Configuration](../reference/configuration.md) for the full list.

## Related

- [Lineage](../concepts/lineage.md) — what the graph means and how it is derived.
- [Import lineage from dbt](import-dbt-lineage.md) — the other way relationships get into the graph.
