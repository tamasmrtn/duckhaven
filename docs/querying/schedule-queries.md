---
type: how-to
---

# Schedule queries

Run a saved query automatically on a cron schedule, such as every night at 02:00, with every run recorded.

A schedule runs one [saved query](saved-queries.md) on one agent, without a worksheet open or an external cron job.

## Before you begin

- **You need:** workspace `writer` or `owner`, a [saved query](saved-queries.md) to run, and an agent you may use.
- **Applies to:** every deployment.

## Add a schedule

1. Open **Schedules** in the left navigation. The **Schedules** tab lists the workspace's schedules, and **Runs** lists
   every scheduled run.
2. Click **New schedule** and fill in:
    - **Saved query**: the query to run.
    - **Cron expression**: five fields, `minute hour day-of-month month day-of-week`, evaluated in **UTC**. `0 2 * * *`
      runs daily at 02:00; `*/15 * * * *` runs every fifteen minutes.
    - **Enabled**: a schedule runs only while enabled. Turn it off to pause without losing it.
    - **Agent**: where each run executes. It starts on the saved query's default agent, and lists only agents you may
      use. A run never moves to a different agent.
3. Save it. The row shows the cron, agent, status, and next and last run. Click a row to edit or remove it.

## Check that it worked

After the next scheduled time, the **Runs** tab lists the run with its status, agent, rows, duration and start time; a
failed run shows its error. Scheduled runs also appear in [History](run-queries.md#find-an-earlier-run) with the
origin **Scheduled**.

## How runs behave

A loop in the control plane checks every 60 seconds by default and starts the schedules that are due, through the same
path as a worksheet run (see [Query execution](query-execution.md)). Across several API replicas exactly one starts each
run. So the finest cadence is once a minute, and:

- **No overlap.** If the previous run is still going when the next is due, the next is skipped.
- **No backfill.** Runs missed while the control plane was down are not replayed; the schedule runs at its next time.
- **No retries.** A failed run waits for the next scheduled time.
- **The same checks as a worksheet.** Each run's SQL is checked when it runs, because the saved query can change after
  the schedule is made. A run the checks refuse fails with the reason, and a schedule for SQL that would be refused
  cannot be created or enabled.
- **The SQL as saved.** A schedule runs the saved query's SQL exactly, with no parameters. Saving over the query changes
  what the schedule runs.

### When the agent is stopped

A stopped [elastic agent](../compute/elastic-compute.md), which is most of the time for a nightly schedule, is started
for the run: the run waits `queued` until the agent connects, then executes. If the agent does not come up within the
provisioning deadline, the run fails instead of blocking later runs.

A regular agent that is offline, or an elastic agent that is running but disconnected, cannot be started by the control
plane, so the run fails.

### Whose access a run uses {#whose-data-grants-a-run-uses}

A schedule has no one behind it when it runs, so:

- **Agent access** is checked against the person who **created the schedule**, on every run.
- **Data access**, in a [scoped catalog](../access/scoped-access.md), is checked against the person who **last changed
  the saved query's SQL** or its default agent. That way, editing a colleague's saved query does not let your SQL run
  with their grants.

## If it goes wrong

### "Schedule owner no longer has access to the configured agent"

The schedule's creator lost the `use` tier on its agent. The schedule stays enabled and is not moved to another agent.
Restore the creator's access and the next run succeeds, or have someone with access create the schedule again, which
then runs as them.

### Runs fail with a grant error after an edit

Someone with narrower grants changed the saved query's SQL, and runs use their grants. Have someone with the right
grants save the query again.

### Runs fail because the agent is offline

The agent is a regular agent that is down, or an elastic agent that is disconnected. Bring it back, or edit the
schedule to use another agent.

### A run is missing

The previous run was still going, so this one was skipped, or the control plane was down at the scheduled time. Check
the previous run's duration in **Runs**.

## Related

- [Saved queries](saved-queries.md) — the query a schedule runs.
- [Configuration → Scheduler](../reference/configuration.md#scheduler) — the loop's settings.
- [High availability](../deployment/high-availability.md) — schedules across replicas.
