# Schedule queries

Run a [saved query](saved-queries.md) automatically on a schedule — for example
"every night at 02:00" — without leaving a worksheet open or wiring up an external
cron job. This is DuckHaven's first step into unattended, scheduled execution
(think of it as the single-task case of a Databricks-style *job*): one saved query,
one cron schedule, with every execution recorded so you can see what ran and when.

## Add a schedule

Open **Schedules** from the left nav. It has two tabs: **Schedules** (your
schedules) and **Runs** (every scheduled run). On the **Schedules** tab click
**New schedule** and set:

- **Saved query** — the saved query this schedule runs. (Save a query first from a
  worksheet if you don't have one yet.)
- **Cron expression** — a standard five-field cron string,
  `minute hour day-of-month month day-of-week`. For example `0 2 * * *` runs daily
  at 02:00, and `*/15 * * * *` runs every fifteen minutes. Cron times are evaluated
  in **UTC**.
- **Enabled** — a schedule only runs while it is enabled. Disable it to pause runs
  without losing the schedule.
- **Agent** — the [agent](../concepts/agents.md) that executes each run. It is
  pre-filled from the saved query's default agent, and the list offers only agents
  you are allowed to run on. A run never silently falls back to a different agent.
  What happens when the chosen one is down depends on which kind it is — see below.

Each schedule row shows its cron, agent, status, and next/last run. Click a row to
edit it (or **Remove** it).

## View run history

The **Runs** tab lists every scheduled run in the workspace, newest first, with
each run's status, agent, rows, duration, and start time. A failed run shows its
error, so a misconfigured schedule (bad agent, query error) is visible rather than
silent. The edit dialog for a single schedule also shows that schedule's recent
runs. Scheduled runs additionally appear in the workspace
[History](run-queries.md), tagged as *scheduled* to distinguish them from queries
you ran by hand.

## How it runs

A background loop in the control plane wakes on a fixed tick (60 seconds by
default) and dispatches any schedules that are due, through the same execution path
as an interactive run (see [Query execution](../concepts/query-execution.md)). A
few behaviors worth knowing:

- **No overlap.** If a schedule's previous run is still running when the next is
  due, the new run is skipped — a slow run never piles up behind itself.
- **No backfill.** If the control plane was down when a run was due, it does not
  replay the missed runs; it simply runs at the next scheduled time.
- **No retries.** A failed run waits for its next scheduled tick. There is no
  separate retry or backoff.

### If the chosen agent is down

[Elastic compute](../concepts/elastic-compute.md) terminates itself once it goes
idle — which, for a schedule that runs nightly, is *most of the time*. So a
stopped elastic agent is **started again** for the run: the control plane
re-provisions it, the run waits `queued` until it dials home, and it executes
then. You can pick a stopped elastic agent when creating the schedule for exactly
this reason. Billing starts when it comes up and stops at its idle timeout, as
usual.

If the agent never comes up within the provisioning deadline, the run is recorded
as **failed** rather than waiting forever — otherwise the no-overlap rule would
block every later run behind it.

A **regular** (operator-run) agent that is offline still records the run as
failed. Nothing in the control plane can start one: it is a host someone else
runs, and it only ever dials in. The same applies to an elastic agent that is
merely disconnected while still running — there is no torn-down instance to
re-provision.

### If your agent access is revoked

A schedule has no one sitting behind it at run time, so it runs as **the person who
created it**. Access to the chosen [agent](../concepts/agents.md#who-can-use-an-agent)
is re-checked on **every** fire, against that person — not captured when the schedule
was saved.

So if the creator later loses access to that agent, its runs start failing with
`Schedule owner no longer has access to the configured agent`, visible in the **Runs**
tab and in History. Two things deliberately do *not* happen:

- The schedule is **not disabled**. Restore the grant and the next run succeeds on its
  own, with nothing to re-enable.
- The run is **not re-routed** to some other agent the creator can still use. Choosing
  an agent is an explicit decision about where work runs, and silently moving it
  somewhere else would be a surprising reinterpretation of that choice.

If the schedule should outlive its creator's access, either grant the creator access
again, or have someone who does hold access recreate the schedule — the new schedule
runs as them.

## Notes

- The finest effective cadence is once per minute (the scheduler tick floor).
- Scheduled queries run the saved SQL **verbatim** — there are no parameters or
  templating. Saving over the query's SQL changes what the schedule runs.
- Operators: the loop is leader-elected across replicas (no double-runs) and is
  tuned by [configuration](../reference/configuration.md#scheduler). See
  [High availability](../deployment/high-availability.md).

## Related

- [Saved queries](saved-queries.md) — create the query a schedule runs.
- [Query execution](../concepts/query-execution.md) — scheduled vs. interactive runs.
