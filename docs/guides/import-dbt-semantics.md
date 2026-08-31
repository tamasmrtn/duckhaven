# Import semantic definitions from dbt

If your metrics are already defined in dbt's semantic layer, DuckHaven can import
them rather than making you write them twice. The artifact is `manifest.json` —
the file `dbt parse` already produces — so publishing costs a CI step, not a
second source of truth.

See [Semantic layer](../concepts/semantic-layer.md) for what these definitions
mean once they are in, and [Import lineage from dbt](import-dbt-lineage.md) for
the sibling importer that reads the same artifact for lineage.

## What you need

- A dbt project with `semantic_models:` and `metrics:` defined, and a
  [time spine model](https://docs.getdbt.com/docs/build/metricflow-time-spine)
  (dbt refuses to parse a semantic layer without one).
- Workspace **writer**, plus `metadata` access to every table the definitions
  bind.

## Publish

```bash
dbt parse                       # writes target/manifest.json
dh semantic import dbt target/manifest.json
```

Without the CLI, the endpoint takes the artifact as its body. Send it as raw bytes
rather than re-encoded JSON, so a parse error points at the line you wrote:

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/imports/dbt" \
  -H 'Content-Type: text/plain' \
  --data-binary @target/manifest.json
```

The response reports what landed and what did not:

```json
{
  "provider": "dbt",
  "created": 1,
  "updated": 0,
  "removed": 0,
  "skipped": [
    {"ref": "metric.shop.avg_order_value", "reason": "unsupported_metric_type_ratio"}
  ]
}
```

A dbt project becomes **one** DuckHaven semantic model, named after the project —
a project *is* the subject area, and dbt expresses no finer grouping.

## How it maps

| dbt | DuckHaven | Notes |
| --- | --- | --- |
| `semantic_models[]` | Dataset | Bound to the relation the model built, from `node_relation` |
| Entity, `type: primary` | Dataset `primary_key` | What makes the dataset safe to join *to* |
| Entity, `type: foreign` / `unique` | Relationship (`many_to_one`) | Joined to whichever dataset declares the matching primary entity |
| Dimension, `type: categorical` | Categorical dimension | Prefixed with the dataset name if two semantic models declare it — see below |
| Dimension, `type: time` | Time dimension | `time_granularity` becomes the supported grains, from that grain upward |
| `defaults.agg_time_dimension` | The dataset's default time axis | |
| Measure `agg` + `expr` | Metric aggregation and expression | dbt splits these across measure and metric; DuckHaven keeps them together |
| Measure `agg_time_dimension` | Metric's bound time axis | The field that decides whether "last month" is right |
| Metric `filter` | Metric filter | dbt's Jinja is translated to SQL — see below |
| Metric `label` / `description` | Display name / description | |

!!! note "A name two semantic models both use gets qualified"
    dbt scopes dimension names per semantic model; DuckHaven scopes them per
    model, and a dbt project becomes one model. So if both `orders` and
    `customers` declare `status`, they arrive as `orders_status` and
    `customers_status`. A name only one semantic model declares keeps its bare
    form, and metrics keep pointing at the right axis either way.

## What is skipped, and why

Anything that cannot be represented faithfully is **skipped and reported** rather
than approximated. Each entry comes back in `skipped` with a reason.

| Reason | Meaning |
| --- | --- |
| `unsupported_metric_type_*` | Ratio, derived, cumulative and conversion metrics. V1 metrics do not compose out of other metrics, so there is nothing honest to map them onto. |
| `unsupported_aggregation_*` | `median`, `percentile` and `sum_boolean` have no V1 equivalent. |
| `untranslatable_filter` | Part of the metric's filter could not be resolved to a column. |
| `no_primary_entity` | A foreign entity nobody declares as a key — there is no provable unique side, so the join could multiply rows. |
| `unknown_catalog` | The relation lives in a catalog this workspace does not attach. |

!!! warning "A metric whose filter cannot be translated is dropped, not imported unfiltered"
    dbt writes filters as Jinja — `{{ Dimension('order__status') }} != 'test'`.
    DuckHaven resolves those references to columns. If any part of a filter cannot
    be resolved, the **whole metric is skipped**. Importing it without its filter
    would produce a definition that quietly computes over more rows than it
    should, and reports nothing — a confidently wrong number, which is worse than
    a missing one. Losing the metric is recoverable; nobody notices a wrong one.

## After importing

Imported definitions arrive as a **draft**. An import is a pipeline publishing,
not a person deciding, so nothing answers a question until somebody publishes it:

```bash
dh semantic validate <project>
dh semantic publish  <project>
```

Validation resolves every binding against the live catalog. Publishing needs
workspace **owner** and is refused while anything is broken.

You do not promote the metrics individually — the model is the gate, and its
metrics ride on that one decision.

## Re-publishing and retiring

By default a payload is treated as dbt's **complete** set for the workspace, and
models it no longer declares are retired. Pass `--reconcile none` when publishing
a subset (`?reconcile=none` over HTTP).

An imported model is **read-only in DuckHaven** — `PATCH` returns 409. A model has
exactly one owner, which is what stops the two copies disagreeing: change it in
dbt and import again.

To remove everything dbt published:

```bash
dh semantic purge --provider dbt
```

## Related

- [Command-line quickstart](../getting-started/cli-quickstart.md) — installing and signing in `dh`
- [Semantic layer](../concepts/semantic-layer.md)
- [Define metrics](define-metrics.md) — authoring in DuckHaven instead
- [Import lineage from dbt](import-dbt-lineage.md)
