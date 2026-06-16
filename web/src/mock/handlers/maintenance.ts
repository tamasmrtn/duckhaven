import { http, HttpResponse } from "msw";
import { httpError } from "../lib/errors";
import {
  HEALTH_TABLES,
  POLICY,
  RECOMMENDATIONS,
  resetMaintenance,
} from "../fixtures/maintenance";
import type {
  HealthBand,
  HealthSummary,
  TableHealth,
} from "@/types/maintenance";

function band(score: number | null): HealthBand {
  if (score == null) return "unknown";
  if (score >= 90) return "healthy";
  if (score >= 70) return "fair";
  return "attention";
}

function aggregate(tables: TableHealth[]): HealthSummary {
  const scored = tables.filter((t) => t.score != null);
  if (scored.length === 0) {
    return {
      score: null,
      band: "unknown",
      table_count: 0,
      attention_count: 0,
      total_data_bytes: 0,
    };
  }
  const totalBytes = scored.reduce((a, t) => a + (t.total_data_bytes ?? 0), 0);
  const score =
    totalBytes > 0
      ? Math.round(
          scored.reduce((a, t) => a + t.score! * (t.total_data_bytes ?? 0), 0) /
            totalBytes,
        )
      : Math.round(scored.reduce((a, t) => a + t.score!, 0) / scored.length);
  return {
    score,
    band: band(score),
    table_count: scored.length,
    attention_count: scored.filter((t) => t.score! < 70).length,
    total_data_bytes: totalBytes,
  };
}

export const maintenanceHandlers = [
  http.get("/api/maintenance/health", () => {
    const summary = aggregate(HEALTH_TABLES);
    return HttpResponse.json({
      summary,
      workspaces: [{ workspace_id: "ws-1", slug: "demo", summary }],
    });
  }),

  http.get("/api/workspaces/:ws/health", () => {
    const bySchema = new Map<string, TableHealth[]>();
    for (const t of HEALTH_TABLES) {
      bySchema.set(t.schema_name, [...(bySchema.get(t.schema_name) ?? []), t]);
    }
    return HttpResponse.json({
      summary: aggregate(HEALTH_TABLES),
      namespaces: [...bySchema].map(([schema_name, rows]) => ({
        schema_name,
        summary: aggregate(rows),
      })),
      tables: [...HEALTH_TABLES].sort(
        (a, b) => (a.score ?? 999) - (b.score ?? 999),
      ),
    });
  }),

  http.get(
    "/api/workspaces/:ws/schemas/:schema/tables/:table/health",
    ({ params }) => {
      const t = HEALTH_TABLES.find((x) => x.table_name === params.table);
      if (!t) return httpError(404, "No health data yet");
      return HttpResponse.json({
        table: t,
        history: [
          {
            scanned_at: new Date(Date.now() - 6e8).toISOString(),
            score: 70,
            total_data_bytes: 30 * 1024 ** 3,
          },
          {
            scanned_at: new Date().toISOString(),
            score: t.score,
            total_data_bytes: t.total_data_bytes,
          },
        ],
        recommendations: RECOMMENDATIONS.filter(
          (r) => r.table_name === params.table && r.status === "open",
        ),
      });
    },
  ),

  http.get("/api/maintenance/recommendations", ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get("status") ?? "open";
    const recs =
      status === "all"
        ? RECOMMENDATIONS
        : RECOMMENDATIONS.filter((r) => r.status === status);
    return HttpResponse.json(recs);
  }),

  http.post("/api/maintenance/recommendations/:id/dismiss", ({ params }) => {
    const rec = RECOMMENDATIONS.find((r) => r.id === params.id);
    if (!rec) return httpError(404, "Recommendation not found");
    rec.status = "dismissed";
    return HttpResponse.json(rec);
  }),

  http.get("/api/admin/maintenance/policy", () => HttpResponse.json(POLICY)),

  http.put("/api/admin/maintenance/policy", async ({ request }) => {
    const body = (await request.json()) as Partial<typeof POLICY>;
    Object.assign(POLICY, body);
    return HttpResponse.json(POLICY);
  }),

  http.post("/api/admin/maintenance/scan", () => {
    resetMaintenance();
    return HttpResponse.json({
      status: "ran",
      dispatched: HEALTH_TABLES.length,
      candidates: HEALTH_TABLES.length,
      stale: HEALTH_TABLES.length,
      deep: true,
    });
  }),
];
