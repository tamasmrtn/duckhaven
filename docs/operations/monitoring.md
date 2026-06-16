# Monitoring

DuckHaven surfaces live compute utilization and a full query audit trail from the admin console.

## Agent utilization

**Admin → Utilization** shows two counters at the top — **running queries** and **queued queries**, aggregated across
[agents](../concepts/agents.md) — plus each agent's active concurrency profile. Agents also report live CPU and memory
utilization on every heartbeat.

A persistently non-zero queued count means an agent is saturated. Either raise its slot count (if per-query memory still
suffices) or add another agent — see [Scaling compute](scaling.md).

## Query history

Every query a member runs is recorded. Each workspace has its own history (newest first), excluding internal queries
such as table-sample previews.

## Audit log

**Admin → Audit** is a global, filterable log of every query — by workspace, agent, user, and time range. Each record
captures the SQL, status, row count, duration, result size, and any error. There is no separate audit table; the audit
log is the query record itself.

## Related

- [Query execution](../concepts/query-execution.md) — what the counters reflect.
- [Operator runbook](runbook.md) — procedures for running the cluster.
