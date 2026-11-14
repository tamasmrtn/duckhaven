---
type: concept
---

# Dashboards

A dashboard is a page of widgets, each showing a number, a chart or a table, written as one
[dashboard file](../reference/dashboard-spec.md). This page explains what happens when someone views one: whose data
each widget reads, where the queries run, and what the [result cache](result-cache.md) saves.

!!! note "What exists today"
    Dashboards are stored and published through the [REST API](../reference/rest-api.md#dashboards). The web app lists
    them under **Dashboards** and shows each one, but cannot edit them. A view is one request that streams each
    widget's data as it arrives, so each widget fills in as its answer comes back.

## Every widget runs as the viewer

A dashboard holds no data and no credentials of its own. When you view one, each widget's query runs as **you**, with
your [grants](../access/scoped-access.md), exactly as the same query would from a worksheet. Two people looking at one
dashboard can see different numbers, or one of them can see a widget that says only "You don't have access to the data
this widget reads". That message names no table, so a widget never reveals what it reads to someone who may not read
it. A metric from a semantic model you cannot see answers the same way.

There is no way to run a widget as its author. When a dashboard must show a figure that its viewers may not compute
themselves, publish the figure instead of the access: see [Showing an aggregate to people who may not read the
rows](#showing-an-aggregate-to-people-who-may-not-read-the-rows).

## What happens when a dashboard opens

1. **The parameters are resolved.** Each parameter takes the value in the request, or its default. A time window is
   worked out in the workspace's time zone, so "last 30 days" ends today there.
2. **Each widget's query is built.** A metric widget is compiled by the [semantic layer](../metadata/semantic-layer.md),
   as `POST …/semantic/compile` would. A dimension parameter filters every widget on its model, except a widget that
   lists it under `ignore`, plots that dimension itself, or holds a metric that cannot reach it. An SQL widget takes
   only the parameters its `params` binds.
3. **Questions are shared.** Widgets that ask one model the same question, with the same window and filters, are asked
   as one query with all of their metrics. Four numbers over one month of orders read the table once. Identical SQL is
   sent once too.
4. **Each query is submitted as yours**, through the checks, the result cache and the compute choice that a worksheet's
   run goes through. Each one is recorded in History with origin `dashboard`, tagged with the dashboard's name, the
   widgets it answers and the view it belongs to.
5. **Results stream back.** A result the cache holds comes back at once. The rest go to an agent, and each widget is
   sent the moment its queries finish, so the first numbers appear before the slowest table.

Closing the page, or the request, cancels every query of that view still waiting or running.

### Where the queries run

The queries the cache cannot answer go to an agent together. An agent that supports
[batches](query-execution.md#batches-of-reads) runs a batch's queries one after another on one connection, which saves
each query its own connection and catalog attach. A view's queries are split across up to `DASHBOARD_MAX_INFLIGHT`
(4) batches, which run side by side; a batch holds at most `DASHBOARD_BATCH_MAX_QUERIES` (20). An older agent gets the
queries one at a time, at most 4 of them running at once. See [Configuration](../reference/configuration.md#dashboards).

When no compute is running, the queries wait for it as any query would, and the view says that compute is starting.
With [elastic compute](../compute/elastic-compute.md) that starts an agent.

### What the cache saves

A dashboard is the same queries again and again, which is what the [result cache](result-cache.md) is for. A widget
whose tables have not changed since anyone last ran its query is answered from the cache, without running anything.

**A view answered entirely from the cache starts no compute.** With elastic compute stopped, a dashboard whose data has
not changed opens without waking an agent. This holds only for results small enough to be kept in the control plane
(1 MiB by default, `RESULT_CACHE_INLINE_MAX_BYTES`). A larger result lives on the agent that produced it, and is gone
once that agent stops.

## What a widget says about itself

Each widget comes back with its rows and with what a reader needs to trust them:

- **Its state:** `ok`, `empty` (the query returned no rows), `error`, or `no_access`. One failing widget does not stop
  the others.
- **Its scope:** the window it covers, in words ("Last 30 days: 15 Oct – 13 Nov 2026"), which parameters filtered it,
  and which did not and why. A bar chart of countries stays whole when a country is picked, and says so.
- **Its definitions:** for a metric widget, each metric's expression, description and caveat, as the semantic layer
  holds them.
- **Its chart:** what to draw, decided by the server from the widget's type and its metrics' formats, such as currency
  and percent. A series keeps its colour as filters change, because the colour follows the series' value, not its rank.
  The exception is two values that would take the same colour: the later one in name order takes the next free colour,
  and can change colour when the other comes and goes. A line with more than 8 series draws the 8 largest; its table
  view has them all.
- **A comparison**, for a number with `compare`: the earlier value, the change, and whether the change is good, which
  follows the metric's `polarity`.
- **Where it came from:** whether the cache answered, the SQL that ran, and the query's id in History.

The event stream itself is described in the [REST API reference](../reference/rest-api.md#running-a-dashboard).

## Filtering a dashboard in the app

A dashboard's parameters appear as filters in a row above its widgets. A time window is picked from a list such as
"Last 30 days" or "Month to date", and a dimension parameter's value from the values its semantic model lists, or typed
when it lists none. Every choice is kept in the page's address as `?p.<parameter>=<value>`, such as
`?p.period=last_7_days&p.channel=Web`, so a filtered view can be bookmarked or shared. Whoever opens the link sees the
numbers their own access allows.

Each widget's menu shows its rows as a table, shows the SQL it ran or opens it in a worksheet, and downloads its rows
as CSV. **Copy link** copies a link that opens the dashboard at that widget. The info button beside the menu shows how
the widget's metrics are defined.

## Trying out a file before saving it

A writer can run a dashboard file without saving it, as a
[preview](../reference/rest-api.md#previewing-a-dashboard-file). A preview runs as the person who made it, exactly as
a dashboard would, so it shows the numbers that person would see, not what its eventual viewers will. Only they can
see it. They replace its file as they edit it, and it expires two hours after it was last changed or run.

## Showing an aggregate to people who may not read the rows

Widgets always run as the viewer, so a dashboard cannot show people a total computed from rows they may not read. When
that is what you need, for example a company-wide revenue figure for people without access to individual orders, make
the aggregate its own table:

1. **Write the aggregate.** A [scheduled query](schedule-queries.md) runs with the grants of whoever saved it, so it
   can write the rollup, for example `CREATE OR REPLACE TABLE reporting.revenue_by_month AS SELECT …`, on whatever
   cadence the figure needs.
2. **Grant the aggregate, not the rows.** In a [scoped catalog](../access/scoped-access.md), give the dashboard's
   audience the `reader` tier on `reporting.revenue_by_month` and nothing on the rows it was computed from.
3. **Point the widget at the aggregate**, as an SQL widget or through a semantic model over the rollup table.

The audience then reads exactly the figure you chose to publish, the schedule's runs show when it was last
refreshed, and a grant change is a visible decision rather than a property of a dashboard.

## Limits

- A dashboard holds at most 40 widgets, and opens slowly above 16.
- A timestamp with a time zone is truncated to its day, week or month in the agent's time zone, while windows are
  worked out in the workspace's. Where the two differ, a row near midnight can fall in a neighbouring period.
- A run waits for each query up to its 10-minute timeout, plus 5 minutes for compute to start.

## Related

- [Dashboard files](../reference/dashboard-spec.md): the file format.
- [REST API](../reference/rest-api.md#dashboards): storing, publishing and running dashboards.
- [Result cache](result-cache.md): when a repeated query is answered without running.
- [Query execution](query-execution.md#batches-of-reads): how an agent runs a batch.
