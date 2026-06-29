# Schedule queries

Run a [saved query](saved-queries.md) automatically on a schedule — for example
"every night at 02:00" — without leaving a worksheet open or wiring up an external
cron job. This is DuckHaven's first step into unattended, scheduled execution
(think of it as the single-task case of a Databricks-style *job*): one saved query,
one cron schedule, with every execution recorded so you can see what ran and when.

## Add a schedule

On the **Saved queries** page, click the schedule (calendar) action on a saved
query. In the dialog you set:

- **Cron expression** — a standard five-field cron string,
  `minute hour day-of-month month day-of-week`. For example `0 2 * * *` runs daily
  at 02:00, and `*/15 * * * *` runs every fifteen minutes. Cron times are evaluated
  in **UTC**.
- **Enabled** — a schedule only runs while it is enabled. Disable it to pause runs
  without losing the schedule.
- **Agent** — the [agent](../concepts/agents.md) that executes each run. It is
  pre-filled from the saved query's default agent, but you can choose any agent. If
  the chosen agent is offline when a run is due, that run is recorded as **failed**
  (it does not silently fall back to another agent).

When enabled, the saved-query card shows the **next run** time.

## View run history

The schedule dialog lists the recent **runs** of that schedule, newest first, with
each run's status, duration, and start time — the same information you'd see for an
interactive query. A failed run shows its error here, so a misconfigured schedule
(bad agent, query error) is visible rather than silent. Scheduled runs also appear
in the workspace [History](run-queries.md), tagged as *scheduled* to distinguish
them from queries you ran by hand.

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
