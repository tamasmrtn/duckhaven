# Read query profiles

After a query finishes, the executing [agent](../concepts/agents.md) captures DuckDB's per-operator execution profile
and ships it to the control plane. There are two ways to view it.

## Where to find it

- **Worksheet → Profile tab** — an inline summary and a collapsible operator tree for the query you just ran, with a
  link to the full profile.
- **Dedicated profile page** — reached by clicking any row in query history. It shows the full query SQL in a
  capped-height panel below the header (with an expand button for statements that don't fit), and an interactive
  operator **graph** (result on top, scans at the bottom; data flows up); click a node for its detail.

## What it shows

- A **summary strip** — latency, CPU time, rows returned, result size, peak memory, the reserved memory and CPU the
  query ran under, spill-to-disk, and bytes read/written.
- **Per-operator metrics** — rows scanned to produced, bytes, a time-share bar, and the operator's join conditions,
  filters, and group keys.
- **Inefficiency highlights** computed from the profile — spills (worth a larger reservation), scan blow-ups (a scan
  reading far more rows than returned), bad cardinality estimates (actual far from the optimizer's estimate), and time
  hotspots. The dedicated page also ranks the most expensive operators.

## Notes

Profiling is on by default and best-effort: a capture failure yields no profile rather than failing the query, and
DDL/DML statements carry no profile. To disable it, set `PROFILING_ENABLED=false` on the agent (see the
[Agent reference](../reference/agent-reference.md)).

## Related

- [Query execution](../concepts/query-execution.md) — how reservations are sized.
