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
```

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

`OTEL_SERVICE_NAME` overrides the reported service name (default `duckhaven-api`). Under HA, each replica reports its
`REPLICA_ID` as `service.instance.id`, so `api-1` and `api-2` are distinguishable inside one trace view.

## What is traced

| Span source | What you see |
|---|---|
| FastAPI (automatic) | One server span per API request, named by route template. Health probes (`/healthz`, `/readyz`) and `/api/metrics` scrapes are excluded. |
| httpx (automatic) | A client span for every request to Apache Polaris and for cross-replica dispatch forwards, as children of the request that caused them. |
| SQLAlchemy (automatic) | A span per database statement, with the SQL as an attribute. |

Trace attributes are allowed to carry high-cardinality values that the
[metrics cardinality policy](monitoring.md#cardinality-policy) bans as labels — a `query_id` on a span is exactly how
you find one query's trace; it only ever costs one trace, not an unbounded metric series.

## Finding a trace

In Grafana: **Explore → Tempo**, then search by service (`duckhaven-api`), span name, or duration. Every trace ID is
also queryable directly (Tempo's TraceQL: `{ .service.name = "duckhaven-api" }`).

## Related

- [Monitoring](monitoring.md) — Prometheus metrics, the other half of observability.
- [Configuration reference](../reference/configuration.md#observability) — the `OTEL_*` knobs.
