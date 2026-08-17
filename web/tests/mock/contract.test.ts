import { describe, it, expect } from "vitest";
import { server } from "@tests/mock/server";
import { http } from "msw";
import { ApiError } from "@/api/client";
import { workspacesApi } from "@/api/workspaces";
import { queriesApi } from "@/api/queries";
import { storageBackendsApi } from "@/api/storage-backends";
import { agentsApi } from "@/api/agents";
import { semanticApi } from "@/api/semantic";

// Each mocked endpoint must mirror the authoritative backend *Out schema. These
// assert the realigned shapes; error paths use the built-in triggers + overrides.

describe("workspaces contract", () => {
  it("members are MemberOut-shaped (workspace_id, user_id, role; no email/name)", async () => {
    const members = await workspacesApi.members("acme-analytics");
    expect(members.length).toBeGreaterThan(0);
    for (const m of members) {
      expect(Object.keys(m).sort()).toEqual([
        "role",
        "user_id",
        "workspace_id",
      ]);
      expect(m.workspace_id).toBe("ws-1");
    }
  });

  it("POST /workspaces creates a name-only workspace with no catalog/storage", async () => {
    const ws = await workspacesApi.create({ slug: "new-ws", name: "new-ws" });
    expect(ws.default_catalog).toBeNull();
    expect(ws.storage_backend_kind).toBeNull();
    expect(ws.id).toBe("ws-new-1"); // deterministic id
  });

  it("POST .../members adds a member statefully", async () => {
    const created = await workspacesApi.members("home-lab");
    const before = created.length;
    await fetch("/api/workspaces/home-lab/members", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "u-2", role: "writer" }),
    });
    const after = await workspacesApi.members("home-lab");
    expect(after).toHaveLength(before + 1);
    expect(after.at(-1)).toMatchObject({ user_id: "u-2", role: "writer" });
  });

  it("404 on unknown workspace with a {detail} envelope", async () => {
    await expect(workspacesApi.members("nope")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });
});

describe("queries contract", () => {
  it("dispatch returns a full QueryOut (202)", async () => {
    const q = await queriesApi.dispatch("acme-analytics", "SELECT 1", "ag-1");
    expect(q).toMatchObject({
      workspace_id: "ws-1",
      user_id: "u-1",
      agent_id: "ag-1",
      sql: "SELECT 1",
      status: "queued",
    });
    expect(q.id).toBe("q-new-1");
    expect(typeof q.started_at).toBe("string");
  });

  it("dispatch rejects sandbox-escaping SQL with a 422", async () => {
    await expect(
      queriesApi.dispatch("acme-analytics", "ATTACH 'evil.db' AS evil", "ag-1"),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });

  it("rows returns 409 while a query is not done", async () => {
    const q = await queriesApi.dispatch("acme-analytics", "SELECT 1", "ag-1");
    await expect(queriesApi.rows(q.id)).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
    });
  });

  it("saved-query created_by is a user id, not an email", async () => {
    const saved = await queriesApi.save("acme-analytics", {
      name: "q",
      sql: "SELECT 1",
    });
    expect(saved.created_by).toBe("u-1");
    expect(saved.id).toBe("sq-new-1");
  });

  it("cross-workspace log filters by user_id and orders started_at DESC", async () => {
    const rows = await queriesApi.listForWorkspace("acme-analytics", {
      all_workspaces: true,
      user_id: "u-1",
    });
    expect(rows.every((r) => r.user_id === "u-1")).toBe(true);
    const times = rows.map((r) => r.started_at);
    expect(times).toEqual([...times].sort((a, b) => b.localeCompare(a)));
  });
});

describe("storage backends contract", () => {
  it("created_by is a user id", async () => {
    const list = await storageBackendsApi.list();
    expect(list.every((b) => b.created_by === "u-1")).toBe(true);
  });

  it("409 when deleting a backend still in use", async () => {
    await expect(storageBackendsApi.remove("sb-1")).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
    });
  });
});

describe("agents contract", () => {
  it("bootstrap token is deterministic", async () => {
    const a = await agentsApi.bootstrap();
    expect(a.token).toBe("dh_boot_seed000000000001");
  });

  it("surfaces a 401 from a handler override", async () => {
    server.use(
      http.get("/api/agents", () => new Response("x", { status: 401 })),
    );
    await expect(agentsApi.list()).rejects.toBeInstanceOf(ApiError);
  });

  it("GET /admin/agents/{id} is AgentOut-shaped", async () => {
    const agent = await agentsApi.adminGet("ag-5");
    expect(agent).toMatchObject({ id: "ag-5", name: "warehouse-a" });
    expect(agent).toHaveProperty("lifecycle");
    expect(agent).toHaveProperty("hourly_cost");
  });

  it("the id route does not shadow its literal siblings", async () => {
    // /metrics is served from a different handler file, so declaration order
    // alone would not keep ":id" from swallowing it.
    await expect(agentsApi.adminGet("ag-5")).resolves.toBeTruthy();
    const metrics = await fetch("/api/admin/agents/metrics").then((r) =>
      r.json(),
    );
    expect(Array.isArray(metrics)).toBe(true);
    const options = await agentsApi.computeOptions();
    expect(options).toHaveProperty("cpu_min");
  });

  it("monitoring mirrors AgentMonitoringOut, on one shared bucket grid", async () => {
    const data = await agentsApi.monitoring("ag-5", "8h");
    expect(Object.keys(data).sort()).toEqual([
      "activity",
      "bucket_seconds",
      "completed_query_count",
      "end",
      "failures",
      "peak_query_count",
      "start",
      "summary",
      "utilization",
      "window",
    ]);
    expect(data.bucket_seconds).toBe(300);
    // Every series is projected onto the same grid — the property that lets the
    // charts be stacked and read against each other.
    const n = data.peak_query_count.length;
    expect(data.completed_query_count).toHaveLength(n);
    expect(data.activity).toHaveLength(n);
    expect(data.utilization).toHaveLength(n);
    expect(Object.keys(data.summary).sort()).toEqual([
      "busy_ratio",
      "completed",
      "failed",
      "idle_timeout_minutes",
      "uptime_s",
    ]);
  });

  it("each window carries the bucket size the backend documents", async () => {
    for (const [window, bucket] of [
      ["1h", 60],
      ["3h", 120],
      ["8h", 300],
      ["12h", 300],
      ["24h", 600],
    ] as const) {
      const data = await agentsApi.monitoring("ag-5", window);
      expect(data.bucket_seconds).toBe(bucket);
      expect(data.peak_query_count.length).toBeGreaterThanOrEqual(60);
      expect(data.peak_query_count.length).toBeLessThanOrEqual(144);
    }
  });

  it("rejects an unknown window with a 422, like the API", async () => {
    await expect(
      agentsApi.monitoring("ag-5", "7d" as "8h"),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });
});

describe("semantic contract", () => {
  it("model summaries are ModelSummaryOut-shaped", async () => {
    const models = await semanticApi.listModels("acme-analytics");
    expect(models.length).toBeGreaterThan(0);
    for (const m of models) {
      expect(Object.keys(m).sort()).toEqual([
        "broken_count",
        "created_at",
        "dataset_count",
        "description",
        "dimension_count",
        "id",
        "metric_count",
        "name",
        "owner_id",
        "provider",
        "slug",
        "status",
        "updated_at",
      ]);
    }
  });

  it("a metric carries its calculation, its time axis and its trust state", async () => {
    const model = await semanticApi.getModel("acme-analytics", "sales");
    const revenue = model.metrics.find((m) => m.name === "revenue")!;
    expect(Object.keys(revenue).sort()).toEqual([
      "agg",
      "caveat",
      "dataset",
      "description",
      "display_name",
      "expr",
      "expression",
      "filter",
      "id",
      "name",
      "status",
      "synonyms",
      "time_dimension",
      "validation_detail",
      "validation_state",
    ]);
    expect(revenue.time_dimension).toBe("event_time");
  });

  it("search returns hits plus an explicit ambiguity list", async () => {
    const result = await semanticApi.search("acme-analytics", "turnover");
    expect(Object.keys(result).sort()).toEqual(["ambiguous", "hits"]);
    expect(result.hits[0]?.name).toBe("revenue");
  });

  it("compile returns SQL without a query id — it does not execute", async () => {
    const compiled = await semanticApi.compile("acme-analytics", {
      model: "sales",
      metrics: ["revenue"],
    });
    expect(Object.keys(compiled).sort()).toEqual([
      "definitions_used",
      "sql",
      "warnings",
    ]);
    expect(compiled).not.toHaveProperty("query_id");
  });

  it("an unknown metric is a 422 naming the ones that exist", async () => {
    await expect(
      semanticApi.compile("acme-analytics", {
        model: "sales",
        metrics: ["profit"],
      }),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });

  it("editing an imported model conflicts rather than silently winning", async () => {
    await expect(
      semanticApi.updateModel("acme-analytics", "marketing", { name: "Mine" }),
    ).rejects.toMatchObject({ name: "ApiError", status: 409 });
  });

  it("a new metric is a draft with a rendered calculation", async () => {
    const created = await semanticApi.addMetric("acme-analytics", "sales", {
      name: "refunds",
      dataset: "events",
      agg: "sum",
      expr: "refund_amount",
      filter: "event_type = 'refund'",
    });
    expect(created.status).toBe("draft");
    expect(created.validation_state).toBe("unchecked");
    expect(created.expression).toBe(
      "SUM(refund_amount) FILTER (WHERE event_type = 'refund')",
    );
  });

  it("a sum with no expression is refused", async () => {
    await expect(
      semanticApi.addMetric("acme-analytics", "sales", {
        name: "bad",
        dataset: "events",
        agg: "sum",
      }),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });

  it("'native' is reserved and cannot be imported", async () => {
    await expect(
      semanticApi.importDocument("acme-analytics", "native", "models: []"),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });
});
