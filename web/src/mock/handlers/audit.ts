import { http, HttpResponse } from "msw";

import { auditEvents } from "../fixtures/audit";
import type { AuditEvent } from "@/types/audit";

/**
 * `GET /api/admin/audit/events`, filtered and paged as the server does it: every
 * filter the page sends narrows the rows, and the cursor is the last row's id,
 * so "Load more" can be exercised against the mock.
 */
export const auditHandlers = [
  http.get("/api/admin/audit/events", ({ request }) => {
    const p = new URL(request.url).searchParams;
    const match = (e: AuditEvent): boolean => {
      const since = p.get("since");
      const until = p.get("until");
      const action = p.get("action");
      const actor = p.get("actor_id");
      const ws = p.get("workspace");
      return (
        (!since || e.event_time >= since) &&
        (!until || e.event_time < until) &&
        (!p.get("category") || e.category === p.get("category")) &&
        (!p.get("outcome") || e.outcome === p.get("outcome")) &&
        (!action ||
          (action.endsWith(".*")
            ? e.action.startsWith(action.slice(0, -1))
            : e.action === action)) &&
        (!actor || e.actor.id === actor || e.actor.on_behalf_of_id === actor) &&
        (!p.get("subject_id") || e.subject.id === p.get("subject_id")) &&
        (!ws || e.workspace.slug === ws || e.workspace.id === ws) &&
        (p.get("self_change") == null ||
          String(e.self_change) === p.get("self_change"))
      );
    };
    const rows = auditEvents().filter(match);
    const cursor = p.get("cursor");
    const start = cursor ? rows.findIndex((e) => e.id === cursor) + 1 : 0;
    const limit = Number(p.get("limit") ?? 100);
    const items = rows.slice(start, start + limit);
    const hasMore = start + limit < rows.length;
    return HttpResponse.json({
      items,
      cursor: hasMore ? items[items.length - 1].id : null,
      has_more: hasMore,
    });
  }),
];
