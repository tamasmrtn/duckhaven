# Semantic layer

A **semantic model** records what your business terms mean and how they are
calculated: that revenue is the sum of `amount` over purchase events, excluding
test accounts, measured on `event_time` and not on `created_at`.

Its purpose is narrow. DuckHaven's catalog already knows a column is called
`total_amount` and holds a decimal. It does not know that summing it is what
your organization calls revenue. Without somewhere to record that, the AI
assistant has to work it out from column names — and a guess that lands on a
plausible-looking wrong column returns a number with no error attached.

## What a semantic model contains

A model is one **subject area** — sales, marketing, support — holding four kinds
of definition.

**Datasets** are logical tables bound to physical ones. A dataset named `orders`
points at `warehouse.analytics.orders` and declares its primary key.

**Dimensions** are the ways a number can be sliced. A dimension is either
*categorical* (country, plan, segment) or a *time* axis, and a time dimension
declares which grains it supports.

**Metrics** are the authoritative answers. A metric names an aggregation, an
expression to aggregate, an optional filter that always applies, and — most
importantly — the time dimension it is measured on.

**Relationships** are declared joins. They always point from a fact table toward
something unique.

## Why a metric is more than a description

A description like "revenue is the money we make from sales" is useful
documentation and useless for computing anything. A metric is *machine-actionable*:

```text
revenue
  on dataset:   orders
  aggregation:  sum
  expression:   total_amount
  filter:       status <> 'test'
  measured on:  order_date
  caveat:       Excludes internal test orders.
```

From that, DuckHaven generates the SQL itself. The assistant chooses *which*
metric to use; it never writes the aggregation. So the failure where one report
says revenue is £2.1M and another says £2.4M because somebody forgot the filter
cannot happen through this path — there is one definition, and it is applied
every time.

## Why joins are declared, and only in one direction

A relationship declares a cardinality, and only two are available:
`many_to_one` and `one_to_one`. There is deliberately no `one_to_many`.

Joining from a fact table toward a table that is *not* unique on the join key
multiplies fact rows. Every `SUM` that crosses that join comes back inflated,
and nothing errors — the query succeeds and the number is simply too big. By
having no vocabulary for that direction, DuckHaven cannot generate such a join.

Validation goes further: a `many_to_one` whose right-hand columns are not that
dataset's declared primary key is rejected, because the claim of uniqueness has
nothing backing it.

Two further rules keep generated SQL checkable by a person:

- Join paths are at most **two hops** from the metric's own dataset.
- If two different paths reach the same dataset, that is an **error**, not a
  choice. An order's country could be the customer's or the shipping address's;
  those are different numbers and only a person knows which was meant.

## Why every metric names a time column

Fact tables routinely carry several dates: when the order was placed, when the
row was written, when it shipped. "Revenue last month" measured on the wrong one
returns a different number and no error, which makes it the most expensive kind
of mistake — the kind nobody notices.

Binding each metric to its measurement axis removes the choice. If a metric has
no time dimension and its dataset has more than one candidate, DuckHaven refuses
a time-filtered question rather than picking.

Time windows are stated explicitly for the same reason. "Last month" means the
previous calendar month to one person, the trailing thirty days to another, and
month-to-date to a third:

| Window | Meaning |
| --- | --- |
| `last_complete` | The N most recent **complete** periods, excluding the one in progress |
| `trailing` | A rolling window of N periods ending today, including today |
| `to_date` | From the start of the current period through today |
| `absolute` | Explicit start and end dates; the end is exclusive |

There is no default.

## Business vocabulary

Definitions carry **synonyms** — the words people actually use. "Turnover",
"GMV" and "top line" all resolve to `revenue` without anybody having to know the
column name.

Dimensions can also carry a few **sample values**. This solves a specific silent
failure: a user asks for customers "in the US", the stored value is
`United States`, and the query returns zero rows with nothing to indicate why.

## Publishing, and what the assistant sees

A model moves through three states:

```text
draft ──(an owner publishes)──> published ──> deprecated
```

**Only published models reach the AI assistant.** That is the whole governance
model, and it is deliberately one boolean rather than an approval workflow: the
minimum useful control is the ability to stop somebody's experiment being quoted
back as what the company means.

Publishing validates first and is refused if anything is broken. Deprecated
models stay readable — old links keep working — but are excluded from new
answers.

Imported models arrive as drafts. An import is a pipeline publishing, not a
person deciding.

## When a definition stops being true

Every binding is checked against the live catalog, never a cached copy. Each
definition carries one of three states:

| State | Meaning |
| --- | --- |
| `ok` | Checked, and every column it names is still there |
| `broken` | Checked, and it no longer resolves |
| `unchecked` | Nothing has looked since something changed |

`unchecked` is not a softer `ok`. It is what a definition becomes when its table
was dropped and recreated, and it means the next read should revalidate rather
than trust the old verdict.

A **broken** definition is withheld from the compiler, which refuses to build
SQL from it. But it is *not* hidden: `search_semantic` reports it separately from
the usable results, with the reason. That distinction matters more than it
looks. Filtering it out silently would have the assistant answer "there is no
revenue metric", which is not merely unhelpful — it sends somebody off to build a
definition the organization already has. Instead the assistant says revenue is
defined but currently broken and why, and does not substitute its own
calculation. A wrong number presented confidently is worse than a refusal, and a
misleading refusal is worse than an accurate one.

Dropping a table marks the definitions bound to it broken rather than deleting
them. Grants and lineage describe the table and stop meaning anything once it is
gone; a metric describes the business and outlives the table it happened to be
bound to.

## How the assistant uses it

Four tools, in the order they are normally called:

1. `search_semantic` — match the question to definitions, by name or synonym.
2. `get_semantic_model` — read the relevant subject area.
3. `query_metric` — compile and run, using the stored definition.
4. `explain_metric` — answer "how is this calculated?" from the definition.

The assistant can still write its own SQL with `run_sql`, and should for
anything the semantic models do not cover. When it aggregates a column that a
published metric already defines, the result comes back with a warning naming
that metric — the query still runs, but both the assistant and the audit trail
record that an agreed definition was bypassed.

!!! note "What this deliberately does not do"
    V1 has no ratio, derived, cumulative or conversion metric types; a metric is
    one aggregation over one dataset. There is no version history — changing a
    definition changes it, though previously-run queries keep their SQL in query
    history. There are no fiscal calendars or custom time spines, and no
    approval workflow beyond publish. Metrics from two different datasets cannot
    be combined in one query.

## Related

- [AI assistant](assistant.md) — how the assistant uses these definitions
- [Lineage](lineage.md) — where a table's data comes from
- [Metadata](metadata.md) — what DuckHaven records about tables
- [Define metrics](../guides/define-metrics.md) — the authoring task
