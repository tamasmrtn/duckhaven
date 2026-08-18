import { http, HttpResponse } from "msw";
import { SEMANTIC_MODELS, reportFor, summarize } from "../fixtures/semantic";
import { nextId } from "../lib/seed";
import type { SemanticHit, SemanticModel } from "@/types/semantic";

function find(slug: string): SemanticModel | undefined {
  return SEMANTIC_MODELS.find((m) => m.slug === slug);
}

/**
 * Mirrors the server's ranking closely enough for the UI to be exercised: exact
 * name and exact synonym dominate, everything else is token overlap, metrics
 * outrank dimensions at equal score, and ties among metrics are reported as
 * ambiguous rather than silently ordered.
 */
function search(
  q: string,
  publishedOnly: boolean,
): { hit: SemanticHit; score: number }[] {
  const words = q.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  if (words.length === 0) return [];
  const phrase = words.join(" ");
  const scored: { hit: SemanticHit; score: number }[] = [];

  for (const model of SEMANTIC_MODELS) {
    if (publishedOnly && model.status !== "published") continue;
    for (const metric of model.metrics) {
      if (publishedOnly && metric.status !== "published") continue;
      if (metric.validation_state === "broken") continue;
      let score = 0;
      const name = metric.name.replace(/_/g, " ");
      if (phrase.includes(name)) score += 100;
      if (metric.synonyms.some((s) => phrase.includes(s.toLowerCase())))
        score += 90;
      score += 12 * words.filter((w) => name.includes(w)).length;
      if (score <= 0) continue;
      scored.push({
        score,
        hit: {
          kind: "metric",
          model: model.slug,
          name: metric.name,
          label: metric.display_name ?? metric.name,
          description: metric.description,
          synonyms: metric.synonyms,
          status: metric.status,
          expression: metric.expression,
          time_dimension: metric.time_dimension,
          caveat: metric.caveat,
        },
      });
    }
    for (const dim of model.dimensions) {
      if (publishedOnly && model.status !== "published") continue;
      let score = 0;
      const name = dim.name.replace(/_/g, " ");
      if (phrase.includes(name)) score += 100;
      if (dim.synonyms.some((s) => phrase.includes(s.toLowerCase())))
        score += 90;
      score += 12 * words.filter((w) => name.includes(w)).length;
      if (score <= 0) continue;
      scored.push({
        score,
        hit: {
          kind: "dimension",
          model: model.slug,
          name: dim.name,
          label: dim.display_name ?? dim.name,
          description: dim.description,
          synonyms: dim.synonyms,
          status: "published",
          dimension_kind: dim.kind,
          sample_values: dim.sample_values.slice(0, 5),
        },
      });
    }
  }

  scored.sort(
    (a, b) =>
      b.score - a.score ||
      Number(a.hit.kind !== "metric") - Number(b.hit.kind !== "metric") ||
      a.hit.model.localeCompare(b.hit.model) ||
      a.hit.name.localeCompare(b.hit.name),
  );
  return scored;
}

/**
 * Mirrors the API: metrics tied at the top of the ranking are a question, not an
 * ordering. Returning the tie is what lets a caller ask instead of picking.
 */
function ambiguous(
  scored: { hit: SemanticHit; score: number }[],
): SemanticHit[] {
  const metrics = scored.filter((s) => s.hit.kind === "metric");
  if (metrics.length < 2) return [];
  const top = metrics[0].score;
  const tied = metrics.filter((s) => s.score === top);
  return tied.length > 1 ? tied.map((s) => s.hit) : [];
}

export const semanticHandlers = [
  http.get("/api/workspaces/:ws/semantic/models", ({ request }) => {
    const status = new URL(request.url).searchParams.get("status");
    const models = status
      ? SEMANTIC_MODELS.filter((m) => m.status === status)
      : SEMANTIC_MODELS;
    return HttpResponse.json(models.map(summarize));
  }),

  http.post("/api/workspaces/:ws/semantic/models", async ({ request }) => {
    const body = (await request.json()) as {
      slug: string;
      name: string;
      description?: string;
    };
    if (find(body.slug)) {
      return HttpResponse.json(
        { detail: `A semantic model called '${body.slug}' already exists.` },
        { status: 409 },
      );
    }
    const model: SemanticModel = {
      id: nextId("sem-model"),
      slug: body.slug,
      name: body.name,
      description: body.description ?? null,
      status: "draft",
      provider: "native",
      owner_id: "user-1",
      metric_count: 0,
      dimension_count: 0,
      dataset_count: 0,
      broken_count: 0,
      created_at: "2026-08-17T09:00:00Z",
      updated_at: "2026-08-17T09:00:00Z",
      datasets: [],
      dimensions: [],
      metrics: [],
      relationships: [],
    };
    SEMANTIC_MODELS.push(model);
    return HttpResponse.json(model, { status: 201 });
  }),

  http.get("/api/workspaces/:ws/semantic/models/:slug", ({ params }) => {
    const model = find(params.slug as string);
    if (!model) {
      return HttpResponse.json(
        { detail: `No semantic model '${params.slug as string}'.` },
        { status: 404 },
      );
    }
    return HttpResponse.json(model);
  }),

  http.patch(
    "/api/workspaces/:ws/semantic/models/:slug",
    async ({ params, request }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      // Mirrors the API: a model has one owner, so an import is edited at its
      // source and never here.
      if (model.provider !== "native") {
        return HttpResponse.json(
          {
            detail: {
              error: "imported_model",
              detail: `'${model.slug}' was imported from '${model.provider}' and is edited there, not here.`,
            },
          },
          { status: 409 },
        );
      }
      const body = (await request.json()) as {
        name?: string;
        description?: string;
      };
      if (body.name != null) model.name = body.name;
      if (body.description != null) model.description = body.description;
      return HttpResponse.json(model);
    },
  ),

  http.delete("/api/workspaces/:ws/semantic/models/:slug", ({ params }) => {
    const index = SEMANTIC_MODELS.findIndex((m) => m.slug === params.slug);
    if (index < 0) return HttpResponse.json({}, { status: 404 });
    SEMANTIC_MODELS.splice(index, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  // Removing a definition. The dataset case mirrors the server's refusal: the
  // real foreign keys cascade, so a permitted delete would take dependents with
  // it, and the UI needs to be exercised against the 409 rather than a success.
  http.delete(
    "/api/workspaces/:ws/semantic/models/:slug/datasets/:name",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const name = params.name as string;
      if (!model.datasets.some((d) => d.name === name))
        return HttpResponse.json({}, { status: 404 });

      const dependents = [
        ...model.dimensions
          .filter((d) => d.dataset === name)
          .map((d) => `dimension '${d.name}'`),
        ...model.metrics
          .filter((m) => m.dataset === name)
          .map((m) => `metric '${m.name}'`),
        ...model.relationships
          .filter((r) => r.left_dataset === name || r.right_dataset === name)
          .map((r) => `relationship '${r.name}'`),
      ];
      if (dependents.length > 0) {
        return HttpResponse.json(
          {
            detail: {
              error: "dataset_in_use",
              detail: `'${name}' still has ${dependents.join(", ")}. Remove them first — deleting the dataset would delete them too.`,
              dependents,
            },
          },
          { status: 409 },
        );
      }
      model.datasets = model.datasets.filter((d) => d.name !== name);
      return new HttpResponse(null, { status: 204 });
    },
  ),

  http.delete(
    "/api/workspaces/:ws/semantic/models/:slug/dimensions/:name",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const name = params.name as string;
      if (!model.dimensions.some((d) => d.name === name))
        return HttpResponse.json({}, { status: 404 });

      // Refused while a metric is measured on it: an absent axis is
      // indistinguishable from one never set, and the compiler answers that
      // kind on the dataset's default date.
      const measuredOn = model.metrics.filter((m) => m.time_dimension === name);
      if (measuredOn.length > 0) {
        const names = measuredOn.map((m) => m.name).sort();
        return HttpResponse.json(
          {
            detail: {
              error: "dimension_in_use",
              detail: `${names.join(", ")} ${names.length === 1 ? "is" : "are"} measured on '${name}'. Rebind or remove ${names.length === 1 ? "it" : "them"} first.`,
              dependents: names.map((n) => `metric '${n}'`),
            },
          },
          { status: 409 },
        );
      }
      model.dimensions = model.dimensions.filter((d) => d.name !== name);
      return new HttpResponse(null, { status: 204 });
    },
  ),

  http.delete(
    "/api/workspaces/:ws/semantic/models/:slug/metrics/:name",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const name = params.name as string;
      if (!model.metrics.some((m) => m.name === name))
        return HttpResponse.json({}, { status: 404 });
      model.metrics = model.metrics.filter((m) => m.name !== name);
      return new HttpResponse(null, { status: 204 });
    },
  ),

  http.delete(
    "/api/workspaces/:ws/semantic/models/:slug/relationships/:name",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const name = params.name as string;
      if (!model.relationships.some((r) => r.name === name))
        return HttpResponse.json({}, { status: 404 });
      model.relationships = model.relationships.filter((r) => r.name !== name);
      return new HttpResponse(null, { status: 204 });
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/datasets",
    async ({ params, request }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const body = (await request.json()) as Record<string, never>;
      const dataset = {
        id: nextId("sem-ds"),
        name: body.name as unknown as string,
        description: (body.description as unknown as string) ?? null,
        synonyms: (body.synonyms as unknown as string[]) ?? [],
        catalog: body.catalog as unknown as string,
        schema_name: body.schema_name as unknown as string,
        table_name: body.table_name as unknown as string,
        primary_key: (body.primary_key as unknown as string[]) ?? [],
        validation_state: "unchecked" as const,
        validation_detail: null,
      };
      model.datasets.push(dataset);
      model.dataset_count = model.datasets.length;
      return HttpResponse.json(dataset, { status: 201 });
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/dimensions",
    async ({ params, request }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const body = (await request.json()) as Record<string, never>;
      const kind = ((body.kind as unknown as string) ?? "categorical") as
        "categorical" | "time";
      const dimension = {
        id: nextId("sem-dim"),
        name: body.name as unknown as string,
        dataset: body.dataset as unknown as string,
        display_name: (body.display_name as unknown as string) ?? null,
        description: (body.description as unknown as string) ?? null,
        synonyms: (body.synonyms as unknown as string[]) ?? [],
        kind,
        expr:
          (body.expr as unknown as string) ?? (body.name as unknown as string),
        data_type: null,
        time_grains:
          kind === "time" ? ["day", "week", "month", "quarter", "year"] : [],
        is_default_time: Boolean(body.is_default_time),
        sample_values: (body.sample_values as unknown as string[]) ?? [],
        validation_state: "unchecked" as const,
        validation_detail: null,
      };
      model.dimensions.push(dimension);
      model.dimension_count = model.dimensions.length;
      return HttpResponse.json(dimension, { status: 201 });
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/metrics",
    async ({ params, request }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const body = (await request.json()) as Record<string, never>;
      const agg = body.agg as unknown as
        "sum" | "count" | "count_distinct" | "avg" | "min" | "max";
      const expr = (body.expr as unknown as string) ?? null;
      // Mirrors the API: only count may omit an expression to aggregate.
      if (agg !== "count" && !expr) {
        return HttpResponse.json(
          {
            detail: `${agg} needs an expression to aggregate; only count may omit one.`,
          },
          { status: 422 },
        );
      }
      const filter = (body.filter as unknown as string) ?? null;
      const inner = expr ?? "*";
      const call =
        agg === "count_distinct"
          ? `COUNT(DISTINCT ${inner})`
          : `${agg.toUpperCase()}(${inner})`;
      const metric = {
        id: nextId("sem-met"),
        name: body.name as unknown as string,
        dataset: body.dataset as unknown as string,
        display_name: (body.display_name as unknown as string) ?? null,
        description: (body.description as unknown as string) ?? null,
        synonyms: (body.synonyms as unknown as string[]) ?? [],
        agg,
        expr,
        filter,
        time_dimension: (body.time_dimension as unknown as string) ?? null,
        caveat: (body.caveat as unknown as string) ?? null,
        // Mirrors the API: a new metric is a draft until somebody publishes it.
        status: "draft" as const,
        expression: filter ? `${call} FILTER (WHERE ${filter})` : call,
        validation_state: "unchecked" as const,
        validation_detail: null,
      };
      model.metrics.push(metric);
      model.metric_count = model.metrics.length;
      return HttpResponse.json(metric, { status: 201 });
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/relationships",
    async ({ params, request }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const body = (await request.json()) as Record<string, never>;
      const relationship = {
        id: nextId("sem-rel"),
        name: body.name as unknown as string,
        left_dataset: body.left_dataset as unknown as string,
        right_dataset: body.right_dataset as unknown as string,
        join_columns:
          (body.join_columns as unknown as { left: string; right: string }[]) ??
          [],
        cardinality: ((body.cardinality as unknown as string) ??
          "many_to_one") as "many_to_one" | "one_to_one",
        validation_state: "unchecked" as const,
        validation_detail: null,
      };
      model.relationships.push(relationship);
      return HttpResponse.json(relationship, { status: 201 });
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/publish",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const report = reportFor(model);
      if (!report.ok) {
        return HttpResponse.json(
          {
            detail: {
              error: "validation_failed",
              detail:
                "This model cannot be published until its definitions resolve.",
              errors: report.errors,
            },
          },
          { status: 422 },
        );
      }
      model.status = "published";
      return HttpResponse.json(model);
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/deprecate",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      model.status = "deprecated";
      return HttpResponse.json(model);
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/models/:slug/validate",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      return HttpResponse.json(reportFor(model));
    },
  ),

  http.get(
    "/api/workspaces/:ws/semantic/models/:slug/metrics/:metric/dimensions",
    ({ params }) => {
      const model = find(params.slug as string);
      if (!model) return HttpResponse.json({}, { status: 404 });
      const metric = model.metrics.find((m) => m.name === params.metric);
      if (!metric) return HttpResponse.json({}, { status: 404 });
      // Mirrors the API: only datasets reachable from the metric's own dataset,
      // following relationships in the many -> one direction.
      const reachable = new Set([metric.dataset]);
      for (const rel of model.relationships) {
        if (reachable.has(rel.left_dataset)) reachable.add(rel.right_dataset);
      }
      return HttpResponse.json(
        model.dimensions
          .filter((d) => reachable.has(d.dataset))
          .map((d) => d.name)
          .sort(),
      );
    },
  ),

  http.get("/api/workspaces/:ws/semantic/search", ({ request }) => {
    const url = new URL(request.url);
    const q = url.searchParams.get("q") ?? "";
    const publishedOnly = url.searchParams.get("published_only") !== "false";
    const scored = search(q, publishedOnly);
    return HttpResponse.json({
      hits: scored.map((s) => s.hit),
      ambiguous: ambiguous(scored),
    });
  }),

  http.post("/api/workspaces/:ws/semantic/compile", async ({ request }) => {
    const body = (await request.json()) as {
      model: string;
      metrics: string[];
      dimensions?: string[];
      grain?: string;
    };
    const model = find(body.model);
    if (!model) return HttpResponse.json({}, { status: 404 });

    const unknown = body.metrics.filter(
      (name) => !model.metrics.some((m) => m.name === name),
    );
    if (unknown.length > 0) {
      return HttpResponse.json(
        {
          detail: {
            error: "semantic_error",
            detail: `'${model.slug}' has no metric called '${unknown[0]}'. Available: ${model.metrics
              .map((m) => m.name)
              .sort()
              .join(", ")}.`,
          },
        },
        { status: 422 },
      );
    }

    const chosen = model.metrics.filter((m) => body.metrics.includes(m.name));
    const dims = (body.dimensions ?? []).map((name) => {
      const dim = model.dimensions.find((d) => d.name === name);
      return `  ${dim?.dataset ?? "orders"}.${name} AS ${name}`;
    });
    const grainLine = body.grain
      ? [
          `  DATE_TRUNC('${body.grain.toUpperCase()}', orders.order_date) AS ${body.grain}`,
        ]
      : [];
    const selects = [
      ...dims,
      ...grainLine,
      ...chosen.map(
        (m) =>
          `  ${(m.expression ?? "").replace(/\(/, "(orders.")} AS ${m.name}`,
      ),
    ];
    const sql = [
      "SELECT",
      selects.join(",\n"),
      `FROM ${model.datasets[0]?.catalog}.${model.datasets[0]?.schema_name}.${model.datasets[0]?.table_name} AS orders`,
      "LIMIT 500",
    ].join("\n");

    return HttpResponse.json({
      sql,
      definitions_used: chosen.map((m) => ({
        kind: "metric",
        model: model.slug,
        name: m.name,
        description: m.description,
        expression: m.expression,
        caveat: m.caveat,
      })),
      warnings: chosen
        .filter((m) => m.caveat)
        .map((m) => `${m.display_name ?? m.name}: ${m.caveat}`),
    });
  }),

  http.get(
    "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/semantic",
    ({ params }) => {
      const dependents = [];
      for (const model of SEMANTIC_MODELS) {
        for (const dataset of model.datasets) {
          if (
            dataset.schema_name !== params.schema ||
            dataset.table_name !== params.table
          )
            continue;
          for (const metric of model.metrics) {
            if (metric.dataset !== dataset.name) continue;
            dependents.push({
              kind: "metric" as const,
              model: model.slug,
              model_name: model.name,
              model_status: model.status,
              name: metric.name,
              label: metric.display_name ?? metric.name,
              status: metric.status,
              dataset: dataset.name,
              columns: [metric.expr, metric.filter]
                .filter(Boolean)
                .flatMap((e) => (e as string).match(/[a-z_]+/g) ?? []),
            });
          }
        }
      }
      return HttpResponse.json({ dependents });
    },
  ),

  http.post(
    "/api/workspaces/:ws/semantic/imports/:provider",
    async ({ params }) => {
      if (params.provider === "native") {
        return HttpResponse.json(
          {
            detail:
              "'native' is reserved for definitions authored in DuckHaven and cannot be imported.",
          },
          { status: 422 },
        );
      }
      return HttpResponse.json({
        provider: params.provider as string,
        run_id: nextId("run"),
        created: 1,
        updated: 0,
        removed: 0,
        skipped: [],
      });
    },
  ),
];
