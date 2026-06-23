# Configuration reference

Every DuckHaven environment variable, in one place. The control plane is configured through the Compose stack's `.env`
file (all values are optional — the stack auto-generates persistent secrets on first boot). Agents are configured
through their own environment.

For task-oriented guidance see [Install](../deployment/install.md) and the
[Operator runbook](../operations/runbook.md); for the canonical agent details see the
[Agent reference](agent-reference.md).

## Control plane (Compose `.env`)

Source of truth: `deploy/.env.example`. With no `.env` at all, the stack auto-generates persistent secrets on first
boot, so every variable below is optional.

| Variable | Default | Description |
|---|---|---|
| `DUCKHAVEN_IMAGE_TAG` | `latest` | Pin a specific image tag (e.g. `v1.2.3`) instead of riding `:latest`. |
| `SECRET_KEY` | generated | App secret. Captured to the secrets dir on first boot by the API entrypoint and reused thereafter. |
| `POSTGRES_PASSWORD` | `duckhaven` | Internal Postgres password. Postgres publishes no port; shared with the polaris and api services via Compose interpolation. |
| `POLARIS_IMAGE_TAG` | `latest` | Apache Polaris image tag. Pin in production. |
| `POLARIS_REALM` | `POLARIS` | Polaris realm name. |
| `POLARIS_CLIENT_ID` | `root` | Polaris OAuth2 client id used by the API. |
| `POLARIS_CLIENT_SECRET` | `s3cr3t` | Polaris OAuth2 client secret. Override in production. |
| `MINIO_IMAGE_TAG` | `latest` | Bundled MinIO image tag. |
| `MINIO_ROOT_USER` | `minioadmin` | MinIO root user (backs the `object_store` storage backends). |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | MinIO root password. Override in production. |
| `S3_BUCKET` | `warehouse` | Bucket that backs the bundled object store. |
| `S3_REGION` | `us-east-1` | Region reported to DuckDB / Polaris. |
| `S3_ENDPOINT` | `http://minio:9000` | The MinIO URL Polaris vends to DuckDB. For agents on **other** hosts, set this to an address reachable from the agent host. |
| `S3_ENDPOINT_INTERNAL` | `http://minio:9000` | The endpoint Polaris uses inside the Compose network; rarely needs changing. |
| `COOKIE_SECURE` | `false` | Set `true` only when the API is served over HTTPS (a TLS terminator in front), so session cookies are `Secure`-flagged. |
| `AGENT_BOOTSTRAP_TOKEN` | `dh_boot_localdev_seed` | Single-use token the API seeds on startup so the bundled in-stack agent auto-registers. Override in production. |

### Maintenance advisor

Gates and tunes the background [maintenance advisor](../concepts/maintenance.md) scanner that runs inside the API
process. The runtime cadence (off/hourly/daily) and profile are set at runtime in **Admin → Maintenance**, not here —
these variables only control the loop itself. The scanner assumes a single API replica.

| Variable | Default | Description |
|---|---|---|
| `MAINTENANCE_SCANNER_ENABLED` | `true` | Master switch for the background scanner loop. Set `false` to disable scanning entirely (e.g. when running multiple API replicas). |
| `MAINTENANCE_SCAN_TICK_S` | `900` | How often (seconds) the loop wakes to check whether a scan is due per the runtime cadence. |
| `MAINTENANCE_DEEP_SCAN_INTERVAL_S` | `604800` (7 days) | How often the expensive orphan/storage tier runs; cheap metadata probes run every due cycle. |
| `SYSTEM_CATALOG_SYNC_ENABLED` | `true` | Master switch for the [system catalog](system-catalog.md) materializer loop. Set `false` to disable it (e.g. when running multiple API replicas — keep it on exactly one). |
| `SYSTEM_CATALOG_SYNC_INTERVAL_S` | `60` | How often (seconds) query history/audit are copied into the system catalog — its freshness/latency contract. |

## Agent

Source of truth: the [Agent reference](agent-reference.md). An agent needs only `CONTROL_PLANE_URL` and a
`BOOTSTRAP_TOKEN`; everything else has a working default.

| Variable | Required | Default | Description |
|---|---|---|---|
| `CONTROL_PLANE_URL` | Yes | — | WebSocket URL to the control plane's agent endpoint. |
| `BOOTSTRAP_TOKEN` | Yes | — | One-time bootstrap token from the admin UI. |
| `RESULTS_DIR` | No | `/var/duckhaven-agent/results` | Directory for materialized query results. |
| `RESULTS_HTTP_PORT` | No | `8001` | Port for the local HTTP result server. |
| `MEMORY_LIMIT_BYTES` | No | `6442450944` (6 GB) | Per-query memory ceiling. |
| `MAX_CONCURRENCY_PROFILE` | No | `auto` | Reservation sizing: `auto` (EXPLAIN-estimated per query) or a static slot ladder (`single`/`equal_2`/`decaying_2`/`decaying_3`). See [Runbook §6](../operations/runbook.md#6-query-queueing-concurrency). |
| `PROFILING_ENABLED` | No | `true` | Capture DuckDB's post-execution query profile and return it on `query_done`. Set `false` to disable. |

### `auto` estimator knobs

Best-effort knobs that only apply under the `auto` profile. See
[Runbook §6](../operations/runbook.md#6-query-queueing-concurrency).

| Variable | Default | Description |
|---|---|---|
| `ESTIMATE_SAFETY_MULTIPLIER` | `1.5` | Multiplies the raw EXPLAIN estimate to absorb under-estimation. |
| `ESTIMATE_FLOOR_BYTES` | `64 MiB` | Minimum reservation, so a tiny estimate still gets a usable slice. |
| `ESTIMATE_CEILING_FRACTION` | `1.0` | Caps a reservation at this fraction of the budget. |
| `EXPLAIN_TIMEOUT_S` | `2.0` | Time budget for the pre-run `EXPLAIN`; on timeout the query uses the fallback bucket. |
| `ESTIMATE_FALLBACK_BUCKET` | `M` | Bucket used when a query is unestimable (DDL/DML, multi-statement, EXPLAIN error/timeout). |

### Queueing knobs

| Variable | Default | Description |
|---|---|---|
| `MEMORY_HEADROOM_FRACTION` | `0.10` | Fraction of the effective memory budget held back as headroom. |
| `MAX_QUEUE_DEPTH` | `100` | Maximum queued queries; beyond this a query fails with `queue full`. |
| `QUEUED_TIMEOUT_S` | `0` (off) | Fails a query that waits longer than this with `queued timeout`. |

### Operator ceilings

Operator-set ceilings that per-query requests cannot exceed — see the
[Operator runbook](../operations/runbook.md#2-register-two-agents-multi-agent-m4-target).

| Variable | Example | Description |
|---|---|---|
| `MAX_MEMORY_LIMIT_GB` | `6` | Hard upper bound on a query's memory limit. |
| `MAX_TIMEOUT_S` | `600` | Hard upper bound on a query's wall-clock timeout. |
| `RESULT_RETENTION_HOURS` | `24` | How long materialized result Parquet files are kept before the retention sweep removes them. |
