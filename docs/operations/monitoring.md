# Monitoring

DuckHaven surfaces live compute utilization from the admin console, and a full query audit trail from the
**History** page.

## Agent utilization

**Admin → Utilization** shows two counters at the top — **running queries** and **queued queries**, aggregated across
[agents](../concepts/agents.md) — plus each agent's active concurrency profile. Agents also report live CPU and memory
utilization on every heartbeat.

A persistently non-zero queued count means an agent is saturated. Either raise its slot count (if per-query memory still
suffices) or add another agent — see [Scaling compute](scaling.md).

## Query history and audit log

Every query a member runs is recorded on the **History** page (newest first), excluding internal queries such as
table-sample previews. Each member sees the history for the workspace they are in, with a row click opening the
query's profile.

Administrators get an extra **This workspace / All workspaces** toggle on the same page. Switching to **All
workspaces** turns History into the global audit log: every query across every workspace, with a Workspace column and
a filter by user. This is admin-only — a member can never read another workspace's queries. Each record captures the
SQL, status, row count, duration, result size, and any error. There is no separate audit table; the audit log is the
query record itself.

## Related

- [Query execution](../concepts/query-execution.md) — what the counters reflect.
- [Operator runbook](runbook.md) — procedures for running the cluster.
