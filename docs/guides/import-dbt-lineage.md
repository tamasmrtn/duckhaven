# Import lineage from dbt

If your transformations already live in a dbt project, its DAG is a description of your lineage that somebody has
already written down. DuckHaven can import it, so the catalog shows the relationships your project declares alongside
the ones DuckHaven [observes for itself](../concepts/lineage.md).

## What you need

The `target/manifest.json` your project writes on every `dbt` command except `deps`, `clean`, `debug` and `init`, and
**writer** access to the workspace.

## Import it

```sh
curl -X POST \
  -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  -H "Content-Type: application/json" \
  --data @target/manifest.json \
  https://duckhaven.internal/api/workspaces/<workspace>/lineage/imports/dbt
```

The response says what changed:

```json
{ "created": 24, "updated": 3, "removed": 1, "skipped": [] }
```

Open any imported table in the catalog explorer and the Lineage tab shows the graph, with each edge labelled `dbt`.

## How your project maps onto the catalog

DuckHaven reads the physical relation behind each dbt resource — its `database`, `schema` and `alias` — rather than the
model name, so a model with a configured `alias` points at the table it actually writes.

| In dbt | In DuckHaven |
|---|---|
| `database` | the catalog slug |
| `schema` | the schema |
| `alias` (or `name`) | the table |
| `ref()` / `source()` dependencies | edges into that model |
| `invocation_id` | the import batch |

**Models, seeds, snapshots and sources** become part of the graph. **Tests, analyses, operations, macros and metrics**
do not — they produce no persistent dataset, so a model depending on a test says nothing about where its data came
from. Disabled resources are skipped.

### Sources outside DuckHaven

A dbt `source` whose `database` is not one of the workspace's catalogs is kept as an **external asset**, labelled with
that database name. This is the usual case for a source that lands in your warehouse from somewhere else: the graph
keeps its roots instead of starting halfway through the pipeline.

A *model* targeting an unknown catalog is treated differently — it is skipped and reported in `skipped`, because
DuckHaven cannot be building a table in a catalog it does not attach. Usually this means the workspace is missing a
catalog attachment, or the project's `database` does not match the catalog's slug.

## Re-importing

Re-import after every production run; it is idempotent. An import prunes **dbt** edges that this run no longer
declares, scoped to the models the run actually mentions — so `dbt run --select one_model` will not delete lineage for
models it did not build.

Reconciliation never touches edges from another producer. Lineage DuckHaven derived itself always survives an import.

To automate it, add the `curl` above to whatever runs your project, after `dbt run`.

## Retiring the integration

If you stop running dbt, remove its edges so the graph stops claiming relationships nothing will refresh:

```sh
curl -X DELETE \
  -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  "https://duckhaven.internal/api/workspaces/<workspace>/lineage/imports?provider=dbt"
```

This requires workspace **owner**.

## Notes

- **Column-level lineage is not imported**, because `manifest.json` does not contain it — it records column
  *definitions*, not column-to-column derivation. The import is table-level, like the rest of DuckHaven's lineage today.
- `catalog.json` is not used. It holds column types and table statistics, and no dependencies.
- Very large projects: a single import is capped at 5,000 edges. Beyond that, use the generic
  `POST /workspaces/<ws>/lineage/imports` endpoint and send edges in batches.
- If your dbt project runs *against* DuckHaven through a [SQL session](../concepts/sql-sessions.md), you will also get
  execution-derived lineage for free. The two agree most of the time; where they do not, both are shown, which is
  usually worth investigating.

## Related

- [Lineage](../concepts/lineage.md) — how the graph works and what it means.
- [SQL sessions](../concepts/sql-sessions.md) — connecting dbt to DuckHaven.
- [Service accounts](service-accounts.md) — minting a token for automation.
