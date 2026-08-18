# Define metrics

This guide walks through creating a semantic model, defining a metric, and
publishing it so the AI assistant will use it.

See [Semantic layer](../concepts/semantic-layer.md) for what these definitions
mean and why they are shaped this way.

## Before you start

You need workspace **writer** to author definitions and **owner** to publish
them. You also need at least `metadata` access to every table the model binds —
the same access reading that table's schema needs.

## 1. Create a model

**Semantic models** → **New model**. A model is one subject area. Keep it
focused: accuracy falls off noticeably past about ten tables, and the fix is to
split it rather than to grow it.

```text
Identifier:    sales
Display name:  Sales
```

The identifier is used in URLs and by the assistant. It can be renamed later
without breaking anything — the model's identity is not its name.

## 2. Bind datasets

A dataset is a logical table pointing at a physical one.

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/datasets" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "orders",
        "catalog": "warehouse",
        "schema_name": "analytics",
        "table_name": "orders",
        "primary_key": ["id"]
      }'
```

Declare `primary_key` on any dataset you intend to join *to*. Without it, that
dataset cannot be the unique side of a relationship — a join without a key
cannot promise one match per row.

## 3. Declare joins

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/relationships" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "orders_to_customers",
        "left_dataset": "orders",
        "right_dataset": "customers",
        "join_columns": [{"left": "customer_id", "right": "id"}]
      }'
```

`left` is the many side. Traversal always runs left → right, so put the fact
table on the left. `one_to_many` is not accepted: see
[why joins only point one way](../concepts/semantic-layer.md#why-joins-are-declared-and-only-in-one-direction).

## 4. Add dimensions

At least one time dimension, marked as the dataset's default:

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/dimensions" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "order_date",
        "dataset": "orders",
        "kind": "time",
        "time_grains": ["day", "week", "month", "quarter", "year"],
        "is_default_time": true
      }'
```

And the categorical ones people slice by. Give them synonyms and a few sample
values:

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/dimensions" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "country",
        "dataset": "customers",
        "display_name": "Country",
        "synonyms": ["nation", "market"],
        "sample_values": ["United States", "Canada"]
      }'
```

Sample values are what let "customers in the US" find rows stored as
`United States` instead of returning an empty result with no explanation.

## 5. Define the metric

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/metrics" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "revenue",
        "dataset": "orders",
        "display_name": "Revenue",
        "description": "Net booked revenue from placed orders.",
        "agg": "sum",
        "expr": "total_amount",
        "filter": "status <> '\''test'\''",
        "time_dimension": "order_date",
        "synonyms": ["turnover", "gmv"],
        "caveat": "Excludes internal test orders."
      }'
```

Two fields do the most work here:

- **`filter`** applies every single time this metric is computed. It is where
  "excluding test orders" belongs, so it can never be forgotten.
- **`time_dimension`** decides which column a time filter uses. Without it, a
  question about "last month" may silently measure on the wrong date.

`caveat` is surfaced with every answer the metric produces, so a reader sees it
at the moment they see the number.

### The shortcut: save one from a worksheet

Most metrics are first written as SQL in a worksheet. Rather than retyping the
calculation here, highlight the expression and choose **Save as metric…** from
the worksheet toolbar.

DuckHaven splits what it can prove: `SUM(total_amount) AS revenue` arrives as
`agg: sum`, `expr: total_amount`, named `revenue`. Anything it cannot split
faithfully — an arithmetic expression, two aggregates added together — arrives
whole in the expression field with `sum` as a starting point, for you to correct.
Both fields are on the form, so a wrong reading is visible before you save.

You still choose the model and the dataset, because a worksheet cannot know which
subject area a calculation belongs to. The metric is created as a **draft**, like
any other.

## 6. Validate

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/validate"
```

Validation resolves every binding against the live catalog: that the tables
exist, that every column an expression names is still there, and that each
declared join actually joins on a primary key.

In the UI, **Validate** does the same and lists whatever failed.

## 7. Check the SQL

Open the model and look at the **Preview** under each metric. It shows the SQL
that definition compiles to, generated by the same endpoint the assistant uses —
so what you read is what will run.

Compile without executing at any time:

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/compile?published_only=false" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "sales",
        "metrics": ["revenue"],
        "dimensions": ["country"],
        "grain": "month",
        "time_range": {"kind": "last_complete", "grain": "month", "n": 3}
      }'
```

## 8. Publish

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/models/sales/publish"
```

Publishing needs workspace **owner** and validates first. Until it succeeds, the
assistant does not see the model at all.

## Importing instead of authoring

A whole model can be published from a YAML document, which is the right route
when definitions live in version control:

```yaml
version: 1
models:
  - slug: sales
    name: Sales
    datasets:
      - name: orders
        catalog: warehouse
        schema: analytics
        table: orders
        primary_key: [id]
      - name: customers
        catalog: warehouse
        schema: analytics
        table: customers
        primary_key: [id]
    relationships:
      - name: orders_to_customers
        left: orders
        right: customers
        join: [{ left: customer_id, right: id }]
    dimensions:
      - name: order_date
        dataset: orders
        kind: time
        default_time: true
      - name: country
        dataset: customers
        synonyms: [nation]
        sample_values: ["United States", "Canada"]
    metrics:
      - name: revenue
        dataset: orders
        agg: sum
        expr: total_amount
        filter: "status <> 'test'"
        measured_on: order_date
        synonyms: [turnover]
        caveat: Excludes internal test orders.
```

```bash
curl -X POST "$DH/api/workspaces/$WS/semantic/imports/duckhaven" \
  -H 'Content-Type: text/plain' \
  --data-binary @semantic.yaml
```

Notes on importing:

- By default the payload is treated as the **complete** set for that provider,
  and models it no longer declares are retired. Pass `?reconcile=none` when
  publishing a subset.
- A typo in one metric costs that metric, not the whole file. Anything unusable
  comes back in `skipped` with a reason.
- Imported models are **read-only in the UI**. A model has exactly one owner, so
  edit it at the source and import again.
- Imports arrive as drafts. Publishing stays a person's decision.

## Removing a definition

Definitions are meant to be corrected, so removing one needs no more ceremony
than adding it — workspace **writer**, and the table underneath is never touched:

```bash
curl -X DELETE "$DH/api/workspaces/$WS/semantic/models/sales/metrics/revenue"
```

Two removals have consequences worth knowing before you make them.

**Deleting a dimension is refused while a metric is measured on it.** Rebind the
metric to another time dimension, or remove it, and the delete succeeds. The
reason is worth stating: a metric whose time axis is merely *absent* looks
exactly like one that never had an axis, and the compiler answers that kind
using the dataset's default date. Clearing the binding would therefore start
measuring revenue on `created_at` instead of `order_date` — the same question,
a different number, and no error anywhere. The metric is never deleted as a side
effect either way.

**Deleting a dataset is refused while anything still binds it**, and the error
names every dimension, metric and relationship in the way:

```json
{
  "detail": {
    "error": "dataset_in_use",
    "detail": "'orders' still has dimension 'order_date', metric 'revenue'.",
    "dependents": ["dimension 'order_date'", "metric 'revenue'"]
  }
}
```

The bindings cascade in the database, so allowing this would quietly destroy
every definition on the dataset. Refusing costs one extra step and makes the
blast radius something you chose rather than discovered.

!!! note "Imported models are removed at their source"
    `DELETE` on a definition in an imported model returns **409**, like every
    other edit. Deleting it here would only last until the next import.

## Testing that it works

Ask the assistant a question the model covers:

> What was revenue last month, by country?

The tool-call trail should show `query_metric` naming the model and metric, with
the compiled SQL. If it shows `run_sql` with hand-written aggregation instead,
the metric is probably not published, or its synonyms do not include the words
being used.

## Related

- [Semantic layer](../concepts/semantic-layer.md)
- [Use the AI assistant](using-the-assistant.md)
- [How access works](access-levels.md)
