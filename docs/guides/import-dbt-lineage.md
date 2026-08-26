# Import lineage from dbt

If your transformations already live in a dbt project, its DAG is a description of your lineage that somebody has
already written down. DuckHaven can import it, so the catalog shows the relationships your project declares alongside
the ones DuckHaven [observes for itself](../concepts/lineage.md).

## What you need

The `target/manifest.json` your project writes, and a token with **writer** access to the workspace — see
[Service accounts](service-accounts.md).

## Publishing the manifest

Post the artifact to the dbt import endpoint exactly as dbt wrote it. There is nothing to extract or transform first:

```sh
curl -fsS -X POST \
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

### When to publish

**Publish when the project changes, not when it runs.** The manifest describes the dependencies your *code* declares,
so it changes when someone edits a model — not when a schedule fires. Re-posting an unchanged manifest is harmless but
achieves nothing.

The natural home is therefore the pipeline that deploys the project — a merge to your main branch — rather than the
job that runs it:

```yaml
# CI, on merge. `dbt parse` needs no warehouse: it reads the project and
# writes target/manifest.json without connecting or executing anything.
- run: dbt parse --target prod
- run: |
    curl -fsS -X POST \
      -H "Authorization: Bearer $DUCKHAVEN_PAT" \
      -H "Content-Type: application/json" \
      --data @target/manifest.json \
      "$DUCKHAVEN_URL/api/workspaces/$DUCKHAVEN_WORKSPACE/lineage/imports/dbt"
```

!!! note "Publish once per environment"
    A node's `database` and `schema` come from the dbt target, so `dev` and `prod` produce different manifests
    describing different tables. Publish each one against the workspace that owns those catalogs, using the matching
    `--target`. Publishing a developer's local target into a shared workspace would add that developer's personal
    schema to everyone's graph.

Keep publishing an explicit step rather than something a `dbt run` does implicitly. Lineage is a governance side
effect: if the API is unreachable, that should fail a small visible CI step, not the pipeline that moves your data.

!!! note "A DuckHaven CLI will cover this later"
    Publishing is a plain HTTP POST today, and deliberately so — nothing extra to install. A future DuckHaven CLI will
    wrap it, so credentials and workspace can come from existing configuration instead of being wired into CI by hand.
    The endpoint and its behaviour will not change.

## How your project maps onto the catalog

DuckHaven reads the physical relation behind each dbt resource rather than its model name, so a model with a
configured `alias` points at the table it actually writes. Which field carries that name depends on the resource: a
model, seed or snapshot uses `alias`; a **source** has no `alias` and names its table with `identifier`, where `name`
is only the handle `source('crm', 'customers')` uses.

| In dbt | In DuckHaven |
|---|---|
| `database` | the catalog slug |
| `schema` | the schema |
| `alias` for a model/seed/snapshot, `identifier` for a source | the table |
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

## Re-publishing

Publishing is idempotent: posting the same manifest twice changes nothing except when each relationship was last
seen. Posting a *changed* manifest prunes the **dbt** edges the project no longer declares, scoped to the models the
payload actually mentions — so a manifest covering part of a project will not delete lineage for the rest of it.

Reconciliation never touches edges from another producer. Lineage DuckHaven derived itself always survives an import,
which is what makes the two comparable: if dbt stops declaring a dependency that execution-derived lineage still shows,
the graph keeps both and labels them, and that disagreement is usually worth a look.

## Retiring the integration

If you stop running dbt, remove its edges so the graph stops claiming relationships nothing will refresh:

```sh
curl -X DELETE \
  -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  "https://duckhaven.internal/api/workspaces/<workspace>/lineage/imports?provider=dbt"
```

This requires workspace **owner**.

## Column-level lineage

Publishing `catalog.json` alongside the manifest gets you [column-level
lineage](../concepts/lineage.md#column-level-lineage) for your dbt models as well as table-level.

Generate both, then post them together:

```sh
dbt docs generate                      # writes manifest.json *and* catalog.json

curl -X POST \
  -H "Authorization: Bearer $DUCKHAVEN_PAT" \
  -H "Content-Type: application/json" \
  --data "$(jq -n \
      --slurpfile m target/manifest.json \
      --slurpfile c target/catalog.json \
      '{manifest: $m[0], catalog: $c[0]}')" \
  "https://duckhaven.internal/api/workspaces/<workspace>/lineage/imports/dbt"
```

Posting the manifest on its own still works and still gives you table-level lineage; nothing about the existing request
shape changed.

**Why two artifacts.** dbt does not publish column-to-column derivation — `manifest.json` records column *definitions*,
and column-level lineage is a hosted-platform feature rather than something in the artifacts. What dbt *does* publish is
each model's `compiled_code`: the exact SQL it ran, with every `ref()` and `source()` already resolved. DuckHaven reads
the columns out of that with the same analysis it applies to SQL it runs itself, which needs each upstream relation's
column list — and that is what `catalog.json` carries.

Two things follow from that:

- **`dbt docs generate` is what produces `catalog.json`.** A `dbt compile` or `dbt run` alone writes the manifest but
  not the catalog, so column detail needs the extra step.
- **Only compiled models get column detail.** A model dbt parsed but never compiled has no SQL to read. Its table-level
  edges are unaffected.

Sources outside DuckHaven are a known gap: a model reading a database DuckHaven does not manage keeps its table-level
edge, but the columns coming from it cannot be tied to an asset, so that edge reports column detail as unavailable
rather than claiming nothing flows.

## Notes

- Very large projects: a single import is capped at 5,000 edges. Beyond that, use the generic
  `POST /workspaces/{workspace}/lineage/imports` endpoint and send edges in batches.
- If your dbt project runs *against* DuckHaven through a [SQL session](../concepts/sql-sessions.md), you will also get
  execution-derived lineage for free — no import needed for the models that actually ran. Publishing the manifest is
  still worth it: it covers models a given run did not build, and it is what makes "declared" and "observed"
  comparable. The two agree most of the time; where they do not, both are shown.

## Related

- [Lineage](../concepts/lineage.md) — how the graph works and what it means.
- [SQL sessions](../concepts/sql-sessions.md) — connecting dbt to DuckHaven.
- [Service accounts](service-accounts.md) — minting a token for automation.
