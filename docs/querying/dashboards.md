---
type: concept
---

# Dashboards

A dashboard is a page of widgets, each showing a number, a chart or a table, written as one
[dashboard file](../reference/dashboard-spec.md). This page explains what happens when someone views one: whose data
each widget reads, where the queries run, and what the [result cache](result-cache.md) saves.

!!! note "What exists today"
    Dashboards are made, edited and published in the web app under **Dashboards**, through the
    [REST API](../reference/rest-api.md#dashboards), or [from git](manage-dashboards-in-git.md). A view is one request
    that streams each widget's data as it arrives, so each widget fills in as its answer comes back.

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
each query its own connection and catalog attach. A view sends its queries in as few batches as possible: a batch holds
at most `DASHBOARD_BATCH_MAX_QUERIES` (20), and past that up to `DASHBOARD_MAX_INFLIGHT` (4) batches run side by side.
Opening a connection costs the agent more CPU than a typical widget's query, so for a dashboard of small reads one
batch serves a view sooner than several, and an agent serves about twice as many views. An older agent gets the queries
one at a time, at most 4 of them running at once. See [Configuration](../reference/configuration.md#dashboards).

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
  and can change colour when the other comes and goes. Series come in name order, so the legend keeps its order too. A
  line with more than 8 series draws the 8 largest; its table view has them all.
- **A comparison**, for a number with `compare`: the earlier value, the change, and whether the change is good, which
  follows the metric's `polarity`.
- **Where it came from:** whether the cache answered, the SQL that ran, and the query's id in History.

The event stream itself is described in the [REST API reference](../reference/rest-api.md#running-a-dashboard).

## Filtering a dashboard in the app

A dashboard's parameters appear as filters in a row above its widgets. A time window is picked from a list such as
"Last 30 days" or "Month to date", and a dimension parameter's value from the values its semantic model lists, or typed
when it lists none. Every choice is kept in the page's address as `?p.<parameter>=<value>`, such as
`?p.period=last_7_days&p.channel=Web`, so a filtered view can be bookmarked or shared. Whoever opens the link sees the
numbers their own access allows. In a signed-out browser the link shows the login page, then opens after sign-in.

Each widget's menu shows its rows as a table, shows the SQL it ran or opens it in a worksheet, and downloads its rows
as CSV. **Copy link** copies a link that opens the dashboard at that widget. The info button beside the menu shows how
the widget's metrics are defined. **Ask about this widget** opens the
[assistant](../ai/assistant.md#dashboards-and-charts) to ask about that widget, which it runs as you, with the filters
the page shows.

### Filtering by clicking a bar

A bar chart drawn by a dimension that a parameter filters on can set that parameter. Click the **Germany** bar and
every other widget narrows to Germany, as if it had been picked in the filter row. Click it again to clear the filter.
The bar chart itself keeps every bar and marks the chosen one, because a widget is never filtered by the dimension it
breaks down by. This works for metric widgets whose model and dimension match the parameter's; the filter row sets the
same value from the keyboard. Going back to an earlier filter is answered from the [result cache](result-cache.md)
while the data has not changed.

### Presenting a dashboard

**Present** shows the dashboard alone, over the whole screen, for a wall display or a meeting. Its address carries
`?present=true`, so a display can open straight into it, and **Exit**, Escape or the browser's back button leaves it.

Present mode can refresh the dashboard every 5, 15 or 30 minutes, or every hour, kept in the address as `?refresh=15`.
A refresh is a new view: widgets whose tables have not changed are answered from the cache without starting compute,
and the rest run. A dashboard over data that changes more often than compute's idle timeout keeps compute running for
as long as it refreshes. A browser tab that is hidden skips its refreshes.

## Editing a dashboard in the app

Writers make and edit dashboards in the app. **New dashboard**, on the **Dashboards** page, asks for a title and a name,
then opens a draft holding one text widget. **Edit**, on a dashboard's page, opens its file. Until a dashboard is first
published, only writers see it.

The editor holds the dashboard's file. It completes keys and values, and describes each one, from the
[file's schema](../reference/dashboard-spec.md). As you type, the server checks the file as a save would, and marks
each problem on its line and lists it under the editor.

Beside the file, a [preview](#trying-out-a-file-before-saving-it) runs it a moment after you stop typing. **The
preview runs as you**, so it shows the numbers your access allows; someone with other grants may see other numbers.
While the file has a problem, the preview keeps showing the last file that loaded.

The preview also arranges the file. Each widget's **Arrange** menu moves it left or right, and sets its width from a
quarter of the row to the whole row, or lets it share what the row leaves. Each row moves up or down. These change the
file's text as typing would, and keep its comments.

A dashboard managed in git opens in the editor to be read only, because it changes where its file lives.

### Adding a chart from the assistant

A chart the [assistant](../ai/assistant.md#dashboards-and-charts) draws in the chat can go on a dashboard. **Add to
dashboard**, under the chart, lists the dashboards made in the app; one managed in git is not listed. Picking one opens
its editor with the chart in a new row at the end of the file. Nothing is saved: check the chart in the preview, then
save the draft. If the file has a widget with the chart's id, the chart takes the next free one, such as `trend_2`.
**New dashboard…** starts a draft that holds only the chart.

The widget keeps the SQL of the query the assistant ran. On the dashboard it runs as each viewer, like every widget, so
someone with other grants may see other numbers than the chat showed.

### Changes the assistant proposes

Asked to change a dashboard, the [assistant](../ai/assistant.md#dashboards-and-charts) proposes the change rather than
making it. A proposal is a list of operations on the dashboard's newest file, its draft when there is one: add, update,
move or remove a widget or a parameter, or change the title, description or labels. The server applies them and checks
the file they make as saving does. A problem the change would add, such as a metric that does not exist, refuses the
proposal and tells the assistant what to fix.

The chat shows a card with the proposal's summary and what it changes. **Review changes** opens a page that lists the
changes and runs the file they make, as you. On it:

- **Accept as draft** saves the file as the dashboard's draft, and opens the editor on it. The draft is written as
  canonical YAML, so comments in the draft it replaces are not kept. Publishing it is an owner's decision, as for any
  draft.
- **Discard** leaves the dashboard as it is.
- A dashboard managed in git takes no changes in the app, so its page offers **Copy file** instead: the file the change
  makes, to put in the repository.

A proposal is for the person who asked, and only they can open it. Saving the dashboard after the proposal was made
makes it out of date, so accepting it can never undo someone's edit. A proposal expires a day after it was made.
Proposing needs `writer` for both you and the assistant's service account.

The titles and text on a dashboard are written by anyone who can edit it. The assistant is told never to follow
instructions in them, and a change it proposes is saved only when you accept it.

### Drafts an agent saves over MCP

An agent connected through the [MCP server](../ai/mcp-server.md#dashboards), such as Claude Code, can save a dashboard
file as a draft, as the person whose token it holds. It cannot publish one. The draft waits for a workspace owner to
review and publish it, as any draft does, and readers go on seeing the published version.

The dashboard's page and its editor say that an agent saved the draft over MCP, which client it used, and as whom. The
note goes once anyone saves the draft another way, or it is published or discarded. The number of drafts one person
saves over MCP [is limited](../ai/mcp-server.md#who-saved-a-draft-and-the-limits).

### Drafts and publishing

- **Save draft**, or Ctrl+S (⌘S on a Mac), saves the file as a draft. Readers go on seeing the published version, and
  the workspace's owners see a note on the dashboard that a draft waits.
- **Publish** saves the file and publishes it, with an optional message saying what changed. Only workspace owners
  publish. Publishing checks every widget again, as you, and refuses a file with a problem.
- **Revisions** lists every published version with its message. Opening one loads its file into the editor, and
  publishing it makes it the newest revision.
- **Discard draft** throws the draft away and goes back to the published version.

Changing the file's `name` renames the dashboard: at once if nobody has published it, otherwise when it is next
published. Its old address keeps working.

If someone else saves the dashboard while you edit it, saving says so, and offers to load their version or to save
yours over it. Leaving the editor with unsaved changes asks first.

## Trying out a file before saving it

A writer can run a dashboard file without saving it, as a
[preview](../reference/rest-api.md#previewing-a-dashboard-file); the editor's preview is one. A preview runs as the
person who made it, exactly as a dashboard would, so it shows the numbers that person would see, not what its eventual
viewers will. Only they can see it. They replace its file as they edit it, and it expires two hours after it was last
changed or run. An AI agent connected over [MCP](../ai/mcp-server.md#dashboards) can preview a file the same way.

The web app shows a preview on a page of its own, at `/{workspace}/dashboard-previews/{id}`, for a tool that sends the
file from where it is edited. The page runs the file as you and follows it: a couple of seconds after the preview's
file is replaced, it shows the new file and runs it again. Nothing on the page edits the file. When the preview
expires, the page keeps showing the file as it last was, and says so.

`dh dashboard preview FILE` is such a tool. It holds the file as a preview, prints the address of its page, and sends
the file again each time you save it. `dh dashboard test FILE` runs a file once, as a preview, and reports each widget;
see [Manage dashboards in git](manage-dashboards-in-git.md#check-a-file-as-you-write-it).

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

## When the semantic layer changes

A dashboard reads published models and metrics. A change to the [semantic layer](../metadata/semantic-layer.md) that
removes one a published dashboard reads, such as deleting a metric, deprecating a model, or an import that drops a
dimension, is refused, and the refusal names the dashboards. Change them first, or make the change with
`allow_breaking=true`. See
[Changes that break a dashboard](../metadata/semantic-layer.md#changes-that-break-a-dashboard).
`dh dashboard impact orders.revenue` lists the dashboards that read a model, metric or dimension, and
`dh dashboard exposures` writes the published ones into a dbt project's lineage, as
[exposures](../metadata/import-dbt-lineage.md#show-the-dashboards-in-dbts-lineage).

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
- [AI data assistant](../ai/assistant.md#dashboards-and-charts): asking about a number on a dashboard.
- [MCP server](../ai/mcp-server.md#dashboards): writing and checking a dashboard file from an AI agent.
