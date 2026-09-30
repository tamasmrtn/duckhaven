import { describe, it, expect } from "vitest";
import { server } from "@tests/mock/server";
import { http } from "msw";
import { ApiError } from "@/api/client";
import { workspacesApi } from "@/api/workspaces";
import { queriesApi } from "@/api/queries";
import { storageBackendsApi } from "@/api/storage-backends";
import { agentsApi } from "@/api/agents";
import { semanticApi } from "@/api/semantic";
import { worksheetsApi } from "@/api/worksheets";
import { searchApi } from "@/api/search";

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
    const page = await queriesApi.listForWorkspace("acme-analytics", {
      all_workspaces: true,
      user_id: "u-1",
    });
    // A page envelope, not a bare array: `items` plus the cursor contract.
    expect(Array.isArray(page.items)).toBe(true);
    expect(page).toHaveProperty("has_more");
    const rows = page.items;
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
    const data = await agentsApi.monitoring("ag-5", { window: "8h" });
    expect(Object.keys(data).sort()).toEqual([
      "bucket_seconds",
      "buckets",
      "generated_at",
      "preset",
      "range_end",
      "range_start",
      "spans",
      "summary",
    ]);
    expect(data.bucket_seconds).toBe(300);
    // One flat row per bucket, so every chart indexes the same grid.
    expect(Object.keys(data.buckets[0]).sort()).toEqual([
      "busy_s",
      "cancelled",
      "compute_wait_avg",
      "coverage",
      "cpu_avg",
      "cpu_max",
      "done",
      "down_s",
      "failed",
      "idle_s",
      "mem_avg",
      "mem_max",
      "oom_kills",
      "partial",
      "peak_running",
      "queued_avg",
      "running_avg",
      "seconds",
      "starting_s",
      "t",
      "unknown_s",
      "wait_n",
      "wait_p95_ms",
    ]);
    expect(Object.keys(data.summary).sort()).toEqual([
      "busy_ratio",
      "busy_s",
      "cancelled",
      "cpu_peak",
      "failed",
      "failed_by_reason",
      "finished",
      "idle_s",
      "mem_peak",
      "peak_running",
      "resources_as_of",
      "uptime_s",
      "wait_n",
      "wait_p95_ms",
    ]);
    // The grid covers exactly the range: the buckets' lengths add up to it.
    const span =
      (Date.parse(data.range_end) - Date.parse(data.range_start)) / 1000;
    const covered = data.buckets.reduce((sum, b) => sum + b.seconds, 0);
    expect(covered).toBeCloseTo(span, 0);
  });

  it("each preset carries the bucket size the backend chooses", async () => {
    for (const [window, bucket] of [
      ["1h", 60],
      ["3h", 120],
      ["8h", 300],
      ["12h", 300],
      ["24h", 600],
      ["3d", 1800],
      ["7d", 7200],
    ] as const) {
      const data = await agentsApi.monitoring("ag-5", { window });
      expect(data.bucket_seconds).toBe(bucket);
      expect(data.buckets.length).toBeLessThanOrEqual(151);
    }
  });

  it("rejects what the API rejects with a 422", async () => {
    await expect(
      agentsApi.monitoring("ag-5", { window: "2w" as "8h" }),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
    await expect(
      agentsApi.monitoring("ag-5", {
        start: "2026-09-30T10:00:00Z",
        end: "2026-09-30T10:02:00Z",
      }),
    ).rejects.toMatchObject({ name: "ApiError", status: 422 });
  });

  it("GET /admin/agents/:id/queries is a Page of AgentQueryOut", async () => {
    const page = await agentsApi.queries("ag-5", {
      start: "2026-09-30T10:00:00Z",
      end: "2026-09-30T11:00:00Z",
      sort: "peak_memory",
      dir: "desc",
    });
    expect(Object.keys(page).sort()).toEqual(["cursor", "has_more", "items"]);
    expect(Object.keys(page.items[0]).sort()).toEqual([
      "bytes_read",
      "cpu_time_ms",
      "duration_ms",
      "error",
      "failure_reason",
      "finished_at",
      "id",
      "origin",
      "peak_memory_bytes",
      "row_count",
      "running_at",
      "spill_bytes",
      "sql",
      "started_at",
      "statement_type",
      "status",
      "user_name",
      "wait_ms",
      "workspace_id",
    ]);
  });

  it("GET /runtimes is RuntimeOut-shaped, with exactly one default", async () => {
    const runtimes = await agentsApi.runtimes();
    for (const r of runtimes) {
      expect(Object.keys(r).sort()).toEqual([
        "default",
        "display_name",
        "duckdb_line",
        "ducklake_format",
        "extensions",
        "id",
        "status",
        "upstream_eol",
      ]);
    }
    expect(runtimes.filter((r) => r.default)).toHaveLength(1);
  });

  it("an agent carries AgentRuntimeOut, and bootstrap names its runtime", async () => {
    const agent = await agentsApi.adminGet("ag-5");
    expect(Object.keys(agent.runtime!).sort()).toEqual([
      "default",
      "display_name",
      "id",
      "state",
      "status",
    ]);
    const token = await agentsApi.bootstrap("2.0");
    expect(token.runtime_id).toBe("2.0");
    expect(token.agent_image).toMatch(/-duckdb2\.0$/);
  });

  it("compute options offer runtimes and the default", async () => {
    const options = await agentsApi.computeOptions();
    expect(options.default_runtime).toBe("1.5");
    expect(options.runtimes?.map((r) => r.id)).toContain("1.5");
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

  it("search returns items plus an explicit ambiguity list", async () => {
    const result = await semanticApi.search("acme-analytics", "turnover");
    // `items`, not `hits`: both search endpoints share the shape.
    expect(Object.keys(result).sort()).toEqual(["ambiguous", "items"]);
    expect(result.items[0]?.name).toBe("revenue");
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

describe("worksheets contract", () => {
  it("lists only open tabs, left to right, when asked", async () => {
    const open = await worksheetsApi.listOpen("acme-analytics");
    expect(open.map((w) => w.id)).toEqual(["wk-1", "wk-2"]);
    expect(open.every((w) => w.is_open)).toBe(true);
  });

  it("creates at the end of the tab strip with version 1", async () => {
    const created = await worksheetsApi.create("acme-analytics", {
      title: "New",
    });
    expect(created).toMatchObject({
      id: "wk-new-1",
      title: "New",
      sql: "",
      version: 1,
      tab_position: 2,
      owner_id: "u-1",
    });
  });

  it("bumps the version on a content edit, not on metadata", async () => {
    const edited = await worksheetsApi.update("acme-analytics", "wk-1", {
      sql: "SELECT 2",
      base_version: 1,
    });
    expect(edited.version).toBe(2);
    const meta = await worksheetsApi.update("acme-analytics", "wk-1", {
      catalog: "curated",
    });
    expect(meta.version).toBe(2);
  });

  it("answers a stale base_version with a 409 carrying the current worksheet", async () => {
    await expect(
      worksheetsApi.update("acme-analytics", "wk-2", {
        sql: "SELECT 1",
        base_version: 1,
      }),
    ).rejects.toMatchObject({
      status: 409,
      code: "worksheet_conflict",
      details: { current: { id: "wk-2", version: 3 } },
    });
  });

  it("requires base_version for a content edit", async () => {
    await expect(
      worksheetsApi.update("acme-analytics", "wk-1", { title: "x" }),
    ).rejects.toMatchObject({ status: 422, code: "base_version_required" });
  });
});

describe("saved queries overwrite contract", () => {
  it("on_conflict=error is a 409 naming the existing query", async () => {
    await expect(
      queriesApi.save(
        "acme-analytics",
        { name: "DAILY events", sql: "SELECT 1" },
        "error",
      ),
    ).rejects.toMatchObject({
      status: 409,
      code: "saved_query_exists",
      details: { id: "sq-1", name: "Daily events" },
    });
  });

  it("the default still replaces, ignoring case, and records the editor", async () => {
    const saved = await queriesApi.save("acme-analytics", {
      name: "daily events",
      sql: "SELECT 2",
    });
    expect(saved).toMatchObject({ id: "sq-1", sql: "SELECT 2", updated_by: "u-1" });
  });

  it("dispatch sends timeout_s", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post("/api/workspaces/:ws/queries", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return Response.json({ id: "q" }, { status: 202 });
      }),
    );
    await queriesApi.dispatch("acme-analytics", "SELECT 1", "ag-1", {
      timeout: 90,
    });
    expect(body).toMatchObject({ timeout_s: 90 });
    expect(body).not.toHaveProperty("timeout");
  });
});

describe("search contract", () => {
  it("narrows to the requested types and honours the limit", async () => {
    const report = await searchApi.report("acme-analytics", "e", {
      types: ["table"],
      limit: 2,
    });
    expect(report.items.every((r) => r.type === "table")).toBe(true);
    expect(report.items.length).toBeLessThanOrEqual(2);
    expect(report).toHaveProperty("has_more");
  });

  it("returns the envelope, not a bare array, for a blank query", async () => {
    const res = await fetch("/api/workspaces/acme-analytics/search?q=");
    expect(await res.json()).toEqual({ items: [], has_more: false });
  });
});
