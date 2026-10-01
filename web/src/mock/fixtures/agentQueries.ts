import type { Page } from "@/api/client";
import type { AgentQuery, AgentQuerySort } from "@/types/agent";

// A dozen runs of varied cost, so each sort puts a different one first.
function makeQueries(agentId: string): AgentQuery[] {
  const now = Date.now();
  return Array.from({ length: 12 }, (_, i) => {
    const started = now - (i + 1) * 6 * 60_000;
    const waitMs = i % 4 === 0 ? 2500 : 40;
    const durationMs = 300 + i * 450;
    const failed = i === 3;
    return {
      id: `${agentId}-q${i}`,
      workspace_id: "ws-1",
      user_name: i % 2 ? "Ada" : "Grace",
      sql: `select * from sales where region = ${i}`,
      status: failed ? "failed" : "done",
      origin: null,
      statement_type: "SELECT",
      started_at: new Date(started).toISOString(),
      running_at: new Date(started + waitMs).toISOString(),
      finished_at: new Date(started + waitMs + durationMs).toISOString(),
      duration_ms: durationMs,
      wait_ms: waitMs,
      row_count: 100 * i,
      error: failed ? "Out of Memory Error: could not allocate block" : null,
      failure_reason: failed ? "out_of_memory" : null,
      peak_memory_bytes: i === 5 ? null : ((i * 37) % 12) * 64 * 1024 * 1024,
      cpu_time_ms: i === 5 ? null : durationMs * 1.6,
      spill_bytes: i === 7 ? 512 * 1024 * 1024 : 0,
      bytes_read: i * 10 * 1024 * 1024,
    };
  });
}

const SORT_VALUE: Record<AgentQuerySort, (q: AgentQuery) => number | null> = {
  started_at: (q) => Date.parse(q.started_at),
  duration: (q) => q.duration_ms,
  wait: (q) => q.wait_ms,
  peak_memory: (q) => q.peak_memory_bytes,
  cpu_time: (q) => q.cpu_time_ms,
  spill: (q) => q.spill_bytes,
  bytes_read: (q) => q.bytes_read,
};

const PAGE_SIZE = 50;

/** Worst first, unknowns last — what the API returns. */
export function agentQueriesPage(
  agentId: string,
  sort: AgentQuerySort,
  cursor: string | null,
): Page<AgentQuery> {
  const value = SORT_VALUE[sort];
  const sorted = makeQueries(agentId).sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va == null) return vb == null ? 0 : 1;
    if (vb == null) return -1;
    return vb - va;
  });
  const offset = cursor ? Number(cursor) : 0;
  const items = sorted.slice(offset, offset + PAGE_SIZE);
  const more = offset + PAGE_SIZE < sorted.length;
  return {
    items,
    cursor: more ? String(offset + PAGE_SIZE) : null,
    has_more: more,
  };
}
