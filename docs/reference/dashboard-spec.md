---
type: reference
---

# Dashboard files

A dashboard is one YAML file, `<name>.dashboard.yml`, that lists parameters and rows of widgets. Each widget reads a
metric from the [semantic layer](../metadata/semantic-layer.md) or runs its own SQL. This page is the reference for
the file format, version 1.

!!! note "What exists today"
    The server validates, stores, publishes and [runs](../querying/dashboards.md) dashboard files through the
    [REST API](rest-api.md#dashboards), and publishes the format's JSON Schema at `GET /api/dashboard-schema`.
    Dashboards do not show in the web app yet.

## An example

```yaml
# yaml-language-server: $schema=https://duckhaven.example.com/api/dashboard-schema
version: 1
name: sales-overview
title: Sales overview
description: Revenue and orders for the sales team.
labels: [sales]

parameters:
  period:
    type: time_range
    label: Period
    default: last_30_days
  region:
    type: dimension
    label: Region
    model: orders
    dimension: region

rows:
  - widgets:
      - id: revenue
        type: kpi
        title: Revenue
        width: 4
        query:
          model: orders
          metrics: [revenue]
          time_range: $period
          compare: previous_period
      - id: revenue_trend
        type: line
        title: Revenue by month
        query:
          model: orders
          metrics: [revenue]
          grain: month
          time_range: {kind: last_complete, grain: month, n: 12}
  - widgets:
      - id: by_region
        type: bar
        title: Revenue by region
        width: 6
        query:
          model: orders
          metrics: [revenue]
          dimensions: [region]
          time_range: $period
      - id: refunds
        type: table
        title: Largest refunds
        width: 6
        sql: |
          SELECT order_id, region, amount
          FROM sales.analytics.refunds
          WHERE refunded_on >= $from AND refunded_on < $to
            AND ($region IS NULL OR region = $region)
          ORDER BY amount DESC
        params:
          from: $period.start
          to: $period.end
          region: $region
  - widgets:
      - id: notes
        type: text
        markdown: |
          Revenue is net of refunds. See [how it is defined](/acme/semantic/orders).
```

The first line points an editor's YAML language server at the schema, for completion and inline errors. Replace the
host with your DuckHaven's.

## Top-level keys

| Key | Required | Meaning |
|---|---|---|
| `version` | Yes | The format version, `1`. |
| `name` | Yes | Unique in the workspace: lowercase letters, digits, `-` and `_`, starting with a letter, up to 64 characters. |
| `title` | Yes | Up to 120 characters. |
| `description` | No | Up to 2,000 characters. |
| `labels` | No | Up to 20 short labels, for finding dashboards. |
| `parameters` | No | Up to 8 [parameters](#parameters), by name. |
| `rows` | Yes | 1 to 20 [rows](#rows-and-widths) of widgets. |

Every key in the file must be one this page lists. An unknown key is an error, never ignored, so a typo such as `widht`
is reported with the key it probably meant.

## Parameters

A parameter is a control at the top of the dashboard. Its name is lowercase letters, digits and `_`, starting with a
letter, and widgets refer to it as `$name`.

### Time range parameters

```yaml
period:
  type: time_range
  label: Period
  default: last_30_days
```

`default` is a preset or a window written the way the semantic layer takes one, such as
`{kind: last_complete, grain: month, n: 3}`. Without a default the parameter starts at all time.

| Preset | Window |
|---|---|
| `today` | Today |
| `last_7_days`, `last_30_days`, `last_90_days` | That many days, ending today |
| `last_12_months` | The 12 complete months before this one |
| `week_to_date`, `month_to_date`, `quarter_to_date`, `year_to_date` | From the start of the current period to today |
| `last_week`, `last_month`, `last_quarter`, `last_year` | The previous complete period |

A time range parameter applies only where a widget's query says `time_range: $name`. A six-month trend can sit beside a
month-to-date number on the same dashboard. Dates are worked out in the workspace's
[time zone](../access/workspaces.md#time-zone).

### Dimension parameters

```yaml
region:
  type: dimension
  label: Region
  model: orders
  dimension: region
  default: EMEA
```

A dimension parameter holds one value of one dimension, or none. While it is set, it filters every query widget over the
same `model`, with no extra YAML, except a widget that:

- lists it under `ignore`,
- shows that dimension itself (a bar chart of regions keeps all its bars when a region is picked), or
- has a metric that cannot reach that dimension.

`multiple` is reserved and accepts only `false`.

## Rows and widths

A row holds 1 to 8 widgets side by side, on a 12-column grid. `width` is a widget's share of the 12 columns. Widgets
without a width split whatever is left equally, and a row's widths must leave at least one column for each of them.

A dashboard holds at most 40 widgets. Every widget runs when the dashboard opens, so above 16 it opens slowly and
validation says so as a warning.

## Widgets

Every widget has an `id`, unique in the dashboard, and a `type`. Data widgets also have a `title`, an optional
`description` and an optional `width`.

| Type | With `query`, the query must have | It draws |
|---|---|---|
| `kpi` | One metric, no dimensions, no grain | One number. `compare: previous_period` or `previous_year` adds the same number for an earlier window and the change. |
| `line` | A grain, any metrics, and at most one dimension, which needs exactly one metric | Time across, one line per metric or per value of the dimension. |
| `bar` | Exactly one dimension, no grain, `limit` at most 50 | One bar group per value, largest first, at most 50. |
| `table` | Anything, `limit` at most 1,000 | The rows, dimensions first. Up to 1,000 rows. |
| `text` | No query; `markdown` instead | A heading or a note. |

A data widget reads from exactly one of:

- `query`: a metric query, the same object the [semantic compile endpoint](rest-api.md#semantic-layer) takes. Its
  `time_range` can be `$name` instead of a window. This is the preferred form, because the metric means the same thing
  on every dashboard and carries its own format, such as a currency.
- `sql`: inline SQL, up to 20,000 characters, checked and granted like any other query.

How a widget draws follows from its type and its query; the file has no colour or styling options. Bars stack when
every metric adds up (a `sum` or `count`) and shares one format, so the stack's height is a real total. Bars run
horizontally, ranked, unless the query orders them by their own category.

### SQL widgets

An SQL widget uses `$name` placeholders, as [query parameters](rest-api.md#query-parameters) do, and `params` says which
dashboard parameter each placeholder takes:

| Binding | Value |
|---|---|
| `$region` | The dimension parameter's value, or `NULL` while it is unset |
| `$period.start` | The first day of the window, a `DATE` |
| `$period.end` | The first day after the window, a `DATE`, so compare with `<` |

While a time range is all time, both of its dates are `NULL`. Write the filter so that `NULL` means no filter, as the
example does. An SQL widget is filtered only by what `params` binds, so it takes no `ignore`.

`kpi`, `line` and `bar` SQL widgets name the result columns to draw, in `columns`:

| Type | `columns` |
|---|---|
| `kpi` | `{value: total}` |
| `line` | `{x: day, y: [revenue, cost]}`, or `{x: day, y: [revenue], series: region}` for a line per region |
| `bar` | `{x: country, y: [revenue]}` |

A `table` shows every column, so it takes no `columns`. A query widget takes no `columns` either.

### Text widgets

`markdown` is up to 4,000 characters. A text widget shows no images and no HTML. Links go to `https://` addresses or
to a page of this DuckHaven, written as a path such as `/acme/catalog`.

## How the file is read

The file is YAML, read more strictly than most YAML tools read it:

- Only `true` and `false` are booleans. `yes`, `no`, `on` and `off` stay text, so the country code `NO` is `NO`.
- Numbers are plain decimals. `1:30`, `0x1F` and `1_000` stay text.
- A key that appears twice in one mapping is an error, not "the last one wins".
- Anchors (`&name`), aliases (`*name`), merge keys (`<<`) and tags (`!!python/...`) are refused.
- Types are strict. `width: "6"` is refused; write `width: 6`.
- The file is at most 256 KB and nests at most 12 levels deep.

Every problem comes back at once, each with a path into the file, its line and column, a message, and a hint where there
is a likely fix:

```json
{
  "path": "rows[0].widgets[0].widht",
  "line": 15,
  "col": 9,
  "message": "Unknown key 'widht'.",
  "hint": "Did you mean 'width'?"
}
```

The JSON Schema checks each part's shape. Rules that span the file are checked whenever the dashboard is saved:

- a `$name` names a declared parameter of the right type,
- widget ids are unique, and
- the dashboard is within its limits.

A parameter that no widget uses is a warning.

Publishing also checks the file against the workspace, as the person publishing it: every model it reads is
published, every dimension parameter names a real dimension, every query compiles, and every SQL widget is one
`SELECT`. A draft can fail these, so work in progress can be saved. `POST …/dashboard-validations` runs every check
without storing the file.

## Versions

`version` is the format version the file is written for. A server reads the current version and the one before it.
It reads an older file as if it were current, and never rewrites the file you wrote. A file for a version the server
does not read is refused with **422** `unsupported_spec_version`. The message says whether to upgrade DuckHaven or
the file.

`GET /api/dashboard-schema?version=1` returns the schema for one version. It needs no sign-in, so an editor can fetch
it.

## Related

- [Semantic layer](../metadata/semantic-layer.md): the models, metrics and dimensions widgets read.
- [Dashboards](../querying/dashboards.md): what happens when a dashboard is viewed.
- [REST API](rest-api.md#dashboards): storing, editing, publishing and running dashboards.
- [Errors](errors.md#dashboards): `invalid_dashboard`, `unsupported_spec_version` and the rest.
