# Distributed tracing

DuckHaven emits [OpenTelemetry](https://opentelemetry.io/) traces alongside the
[Prometheus metrics](monitoring.md#prometheus-metrics). Where metrics answer "how many queries failed in the last five
minutes", a trace answers "where did *this* request spend its time" — one timeline per request, spanning the API's
HTTP handling, its database work, and every call it makes to Apache Polaris.

Tracing is additive: the metrics endpoint, its cardinality policy, and the audit log are unchanged.

## Architecture

Both compose stacks ship a small tracing pipeline:

```text
api ──otlp/http──▶ otel-collector ──otlp/grpc──▶ tempo
agent ──otlp/http──┘         ▲
polaris ──otlp/grpc──────────┘
```

A query's trace is not confined to one process: the API dispatches it to an agent over a custom WebSocket protocol,
and the agent hands the actual SQL to DuckDB on a worker thread. Both hops carry the trace forward explicitly (see
[What is traced](#what-is-traced)), so **one trace covers the whole query** — from the HTTP request through DuckDB
execution — not two disconnected halves.

- **`otel-collector`** ([OpenTelemetry Collector](https://opentelemetry.io/docs/collector/)) receives OTLP over HTTP
  (`:4318`) and gRPC (`:4317`) on the compose network and batches spans to Tempo. Config:
  `deploy/otel/otel-collector.yaml`.
- **`tempo`** ([Grafana Tempo](https://grafana.com/oss/tempo/)) stores traces on a local named volume with **72-hour
  retention** (`deploy/tempo/tempo.yaml`). It runs as a single instance in both stacks — the observability plane is
  not HA.
- **`grafana`** is bundled **only in the single-node stack** (`deploy/docker-compose.yml`) as a dev convenience:
  anonymous admin on [http://localhost:3000](http://localhost:3000) with the Tempo datasource pre-provisioned. Do not
  expose it beyond localhost. The HA stack ships no Grafana — add a Tempo datasource pointing at `http://tempo:3200`
  to your own Grafana, next to the Prometheus datasource you already run for the metrics.

To export to a different OTLP backend (another collector, or a SaaS), point `OTEL_EXPORTER_OTLP_ENDPOINT` at it or
edit the collector's exporter block — the services only ever talk to the collector.

## Enabling and disabling

The API exports traces when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (the compose files default it to
`http://otel-collector:4318`). Setting it **empty disables tracing entirely** — no SDK, no exporter, no overhead:

```bash
# deploy/.env
OTEL_EXPORTER_OTLP_ENDPOINT=
```

`OTEL_SERVICE_NAME` overrides the reported service name (default `duckhaven-api`, `duckhaven-agent` for the agent).
Under HA, each API replica reports its `REPLICA_ID` as `service.instance.id`, so `api-1` and `api-2` are
distinguishable inside one trace view; the agent reports its display name the same way.

The agent has the identical `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` knobs, pointed at the same collector
by default.

## What is traced

| Span source | What you see |
|---|---|
| FastAPI (automatic) | One server span per API request, named by route template. Health probes (`/healthz`, `/readyz`) and `/api/metrics` scrapes are excluded. |
| httpx (automatic) | A client span for every request to Apache Polaris and for cross-replica dispatch forwards, as children of the request that caused them. |
| SQLAlchemy (automatic) | A span per database statement, with the SQL as an attribute. |
| `dispatch_query` (manual, api) | Wraps handing a query to an agent over the WebSocket control channel. Its W3C trace context rides inside the `DISPATCH_QUERY` frame so the agent's span below continues the same trace instead of starting a new one. Carries `duckhaven.origin` (`interactive` for a user's query, or the schedule/maintenance origin) so automated runs are distinguishable from clicks. |
| `handle_dispatch` (manual, agent) | The agent's per-query span: admission queueing, running the query, and sending the result back. Continues the api's trace when the frame carried one; starts a fresh trace otherwise (e.g. an older peer without tracing). Failures set the span to an error status. |
| `duckdb.execute` (manual, agent) | Wraps the actual DuckDB execution (offloaded to a worker thread), so per-query execution time is visible directly in the trace rather than only in the aggregate `duckhaven_query_duration_seconds` histogram. Also where the agent hands its active span to DuckDB's own Polaris calls — see [Apache Polaris](#apache-polaris). |
| `assistant.turn` (manual, api) | One span per AI-assistant turn, carrying `duckhaven.conversation_id`, `duckhaven.workspace_id`, and the model name. Pydantic AI's own instrumentation nests under it — an agent-run span, a model-request span per LLM call (with `gen_ai.usage.*` token counts), and a span per tool call — and the assistant's loopback SQL chains on down through `dispatch_query` into the agent. Failures set the span to an error status. See [The AI assistant](#the-ai-assistant). |
| results server (automatic, agent) | One server span per result-page fetch the api makes to the agent's result server, continuing the api's client span. A windowed page adds a `duckdb.slice_parquet` child span for the local slice. |

Trace attributes are allowed to carry high-cardinality values that the
[metrics cardinality policy](monitoring.md#cardinality-policy) bans as labels — a `query_id` on a span is exactly how
you find one query's trace; it only ever costs one trace, not an unbounded metric series.

## Apache Polaris

Polaris's own OTel SDK (built into the vendored Quarkus image) is enabled and points at the same collector, so its
internal Iceberg-catalog spans — namespace/table lookups, credential vending — join the trace of whatever caused them
instead of appearing as one opaque HTTP call. Two call paths reach Polaris, joined by different mechanisms:

- **api → Polaris**, e.g. listing schemas or managing grants: no extra code needed. `HTTPXClientInstrumentor` already
  adds a `traceparent` header to every request the api sends, and Quarkus's server-side instrumentation extracts it and
  continues the same trace, nested under the api's httpx client span.
- **agent → Polaris**: DuckDB's own Iceberg REST catalog client makes these calls (OAuth token exchange,
  namespace/table lookups, credential vending) directly from within the `iceberg`/`httpfs` extensions — an HTTP client
  with no OpenTelemetry instrumentation of its own, so it would otherwise start a disconnected trace per call. Before
  attaching a catalog, the agent captures the active span's W3C carrier (from `handle_dispatch`, or `duckdb.execute` on
  a reused connection) and registers a DuckDB `HTTP`-type secret carrying it as an `EXTRA_HTTP_HEADERS` `traceparent`,
  scoped to the Polaris endpoint. DuckDB attaches that header to every matching request for the life of the
  connection, so Polaris's spans nest under the agent's span instead of starting their own trace. This context must be
  captured on the event-loop thread, before handing the ATTACH work to `run_in_executor` — `contextvars` (and so
  OpenTelemetry's current-span) are not propagated to worker threads.

To turn tracing off for Polaris specifically (leaving the api/agent traced), set `QUARKUS_OTEL_SDK_DISABLED: "true"`
on the `polaris` service in the compose file.

## The AI assistant

When the [AI assistant](../concepts/assistant.md) is enabled, each turn is traced as an `assistant.turn` span with
[Pydantic AI's OpenTelemetry instrumentation](https://ai.pydantic.dev/logfire/) nested underneath, following the
OpenTelemetry [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). You get the model
request timeline, per-tool-call spans, and `gen_ai.usage.*` token counts for the turn, and — because the assistant runs
its SQL through the same governed REST endpoints as any client — the query it triggers continues the same trace right
down to `duckdb.execute` on the agent.

By default the spans **include the turn's content**: the user's prompt, the SQL the model wrote, tool arguments, and the
result samples returned to the model. In a data platform this can be sensitive, and it is written to your trace backend.
To record only the structure — roles, token usage, tool names, timing, and status — set:

```bash
# deploy/.env
ASSISTANT_TRACE_INCLUDE_CONTENT=false
```

## Finding a trace

In Grafana: **Explore → Tempo**, then search by service (`duckhaven-api`), span name, or duration. Every trace ID is
also queryable directly (Tempo's TraceQL: `{ .service.name = "duckhaven-api" }`).

## Correlating logs with traces

Every log line from the api and agent carries the active span's trace and span id:

```text
09:14:02 INFO api.services.query [trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7] dispatched
```

Outside a span (startup, background loops between requests) both fields print as `-` rather than being omitted, so
the log shape stays consistent and grep-able either way. To find every log line for one query: get its trace id from
Tempo (or from any log line for that query) and search `docker compose logs api agent | grep trace_id=<id>` — since
the id is shared, this finds the line on both services even though the query crossed the WebSocket in between.

## Related

- [Monitoring](monitoring.md) — Prometheus metrics, the other half of observability.
- [Configuration reference](../reference/configuration.md#observability) — the `OTEL_*` knobs.
