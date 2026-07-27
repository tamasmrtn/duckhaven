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
| `SETUP_TOKEN` | generated | One-time token gating first-admin creation. The entrypoint writes one to the data directory on first boot; set this instead where that filesystem is ephemeral, so the token survives a replica being replaced. |
| `DATABASE_URL` | built from `POSTGRES_*` | Full SQLAlchemy URL. Set it to override the `POSTGRES_*` variables entirely — required for a connection they cannot express, such as passwordless Microsoft Entra authentication, where there is no password to interpolate. |
| `DB_AUTH_MODE` | `password` | `password` takes the credential from `DATABASE_URL`. `entra` leaves it out and has the driver present a Microsoft Entra access token instead, minted per connection from the ambient managed identity. Requires the server to have Entra authentication enabled and a login role for that identity. |
| `DB_ENTRA_SCOPE` | `https://ossrdbms-aad.database.windows.net/.default` | Token audience for `DB_AUTH_MODE=entra`. Change only for a sovereign cloud. |
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
| `S3_ENDPOINT_INTERNAL` | `http://minio:9000` | The endpoint Polaris uses inside the Compose network, and that the agent's httpfs GET reaches for a presigned staging read; rarely needs changing. |
| `S3_ENDPOINT_PUBLIC` | `http://localhost:9000` (compose); empty in code | Client-facing endpoint used to presign SQL-session staging **upload** (`PUT`) URLs. Empty falls back to `S3_ENDPOINT`. Only differs on the bundled single-host box, where the agent reaches MinIO at `minio:9000` but a dlt client on the host reaches it at `localhost:9000`. Set it to the box's reachable address when running dlt from another host. |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `minioadmin` | MinIO credentials the API uses to presign SQL-session staging URLs for the bundled `object_store` backend (default to `MINIO_ROOT_USER`/`PASSWORD`). External `s3` backends assume their role instead, so these apply only to MinIO. |
| `polaris.features."SUPPORTED_CATALOG_STORAGE_TYPES"` | `["S3","AZURE"]` | Storage types Polaris will provision (set in `docker-compose.yml`). `S3` covers the bundled MinIO and external AWS S3; `AZURE` enables external ADLS Gen2. |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | _(empty)_ | Service principal Polaris uses to mint ADLS Gen2 SAS tokens (Azure `DefaultAzureCredential`). Needs **Storage Blob Data Contributor** on the account. Empty disables ADLS vending; AWS S3 and MinIO are unaffected. |
| `AWS_ENDPOINT_URL_STS` | _(empty)_ | Override the STS endpoint Polaris uses to assume external `s3` roles. Empty = real AWS STS. Set to a private/emulated STS (LocalStack, a VPC STS endpoint, GovCloud) for testing or non-public deployments. |
| `COOKIE_SECURE` | `false` | Set `true` only when the API is served over HTTPS (a TLS terminator in front), so session cookies are `Secure`-flagged. |
| `SESSION_MAX_AGE_SECONDS` | `604800` (7 days) | Session lifetime — drives both the server-side credential expiry and the cookie max-age. Lower it to shorten how long a sign-in lasts. |
| `AGENT_BOOTSTRAP_TOKEN` | `dh_boot_localdev_seed` | Single-use token the API seeds on startup so the bundled in-stack agent auto-registers. Override in production. |

### Identity & SSO

Federated sign-in for [OIDC](../guides/connect-idp.md) and [LDAP/AD](../guides/connect-ldap.md). All optional and off
by default; local accounts always work. Secrets here must never be committed — keep them in your `.env` / secret store.
`SECRET_KEY` (above) also signs the short-lived cookie that holds the transient OIDC handshake state.

| Variable | Default | Description |
|---|---|---|
| `OIDC_PROVIDERS` | `[]` | JSON list of OIDC providers, each a button on the login page. Per-entry keys: `id` (url-safe slug, used in `/api/auth/oidc/<id>/callback`), `label`, `server_metadata_url`, `client_id`, `client_secret`, `scopes` (default `openid email profile`), `groups_claim` (default `groups`), `group_role_map`. Takes precedence over the single-provider fields below. |
| `OIDC_REDIRECT_BASE_URL` | derived | Public base URL (scheme+host) used to build the callback. Required behind a TLS proxy; must match the registered redirect URI's host. |
| `OIDC_ENABLED` | `false` | Single-provider shorthand (back-compat): when `true` and `OIDC_PROVIDERS` is empty, the fields below synthesize one provider with id `sso`. |
| `OIDC_LABEL` | `SSO` | Button label for the shorthand provider, e.g. `Okta` renders "Sign in with Okta". |
| `OIDC_SERVER_METADATA_URL` | — | Shorthand provider discovery document, ending in `/.well-known/openid-configuration`. |
| `OIDC_CLIENT_ID` | — | Shorthand provider confidential client ID. |
| `OIDC_CLIENT_SECRET` | — | Shorthand provider client secret. |
| `OIDC_SCOPES` | `openid email profile groups` | Scopes for the shorthand provider. (Per-provider entries default to `openid email profile`; Entra rejects a `groups` scope.) |
| `OIDC_GROUPS_CLAIM` | `groups` | Shorthand provider ID-token claim holding group memberships. |
| `OIDC_GROUP_ROLE_MAP` | `{}` | Shorthand provider JSON map of group value → global role, e.g. `{"dh-admins": "admin"}`. |
| `LDAP_ENABLED` | `false` | Master switch for LDAP/AD bind authentication. |
| `LDAP_SERVER_URI` | — | `ldaps://host` (port 636) or `ldap://host` (use with `LDAP_USE_START_TLS`). |
| `LDAP_USE_START_TLS` | `false` | Upgrade an `ldap://` connection to TLS via STARTTLS. |
| `LDAP_BIND_DN` | — | DN of the read-only service account used for the user search. |
| `LDAP_BIND_PASSWORD` | — | Password for the service account. |
| `LDAP_USER_SEARCH_BASE` | — | Base DN under which users are searched. |
| `LDAP_USER_FILTER` | `(mail={email})` | Search filter; `{email}` is substituted (and escaped). AD often uses `(sAMAccountName={email})`. |
| `LDAP_EMAIL_ATTR` | `mail` | Attribute read as the user's email. |
| `LDAP_NAME_ATTR` | `displayName` | Attribute read as the user's display name. |
| `LDAP_GROUP_ATTR` | `memberOf` | Attribute holding the user's group DNs. |
| `LDAP_GROUP_ROLE_MAP` | `{}` | JSON object mapping group DN → global role. |
| `LDAP_TLS_CA_CERT` | — | Path to a CA bundle validating the directory's TLS certificate. |
| `LDAP_TIMEOUT_S` | `10` | Connection / receive timeout for directory operations, in seconds. |

### High availability

Only used by the opt-in [HA topology](../deployment/high-availability.md) (multiple API replicas). The single-node
stack ignores them. All have single-replica-safe defaults, so a one-box install behaves identically whether they are set
or not.

| Variable | Default | Description |
|---|---|---|
| `REPLICA_ID` | `api` | Identifier for this API replica, recorded as the owner of agents whose WebSocket it holds. The HA compose sets one per replica (`api-1`, `api-2`). Set to `auto` to use the platform's replica name, falling back to the hostname. |
| `REPLICA_INTERNAL_URL` | `http://localhost:8000` | URL peer replicas use to forward agent-dispatch frames to this replica's private `/internal` endpoints. Set to `auto` on platforms that give every replica identical configuration (Azure Container Apps) to resolve this container's own address — a shared value there breaks forwarding silently, see [High availability](../deployment/high-availability.md#replicas-that-cannot-be-configured-individually). |
| `INTERNAL_API_SECRET` | _(empty)_ | Shared secret guarding the `/internal` cross-replica dispatch endpoints. Must be identical on every replica. When empty, peer forwarding is disabled (single-replica mode). |
| `AGENT_PRESENCE_TTL_S` | `90` | How recently an agent must have pinged for another replica to consider it connected; covers a replica that died without clearing its ownership. |
| `DB_POOL_SIZE` | `5` | SQLAlchemy connection-pool size per replica. Keep `replicas × (DB_POOL_SIZE + DB_MAX_OVERFLOW)` under the Postgres `max_connections`. |
| `DB_MAX_OVERFLOW` | `10` | Extra connections each replica may open above `DB_POOL_SIZE` under load. |
| `DB_POOL_RECYCLE_S` | `1800` | Recycle pooled connections older than this (seconds). With `pool_pre_ping`, this is what makes Postgres failover transparent. |

### Maintenance advisor

Gates and tunes the background [maintenance advisor](../concepts/maintenance.md) scanner that runs inside the API
process. The runtime cadence (off/hourly/daily) and profile are set at runtime in **Admin → Maintenance**, not here —
these variables only control the loop itself. Across multiple API replicas the scanner is leader-elected via a Postgres
advisory lock, so only one cycle runs at a time — leave it enabled everywhere (see
[High availability](../deployment/high-availability.md)).

| Variable | Default | Description |
|---|---|---|
| `MAINTENANCE_SCANNER_ENABLED` | `true` | Whether this replica participates in the (leader-elected) scanner loop. Safe to leave `true` on every replica; set `false` only to exclude a replica entirely. |
| `MAINTENANCE_SCAN_TICK_S` | `900` | How often (seconds) the loop wakes to check whether a scan is due per the runtime cadence. |
| `MAINTENANCE_DEEP_SCAN_INTERVAL_S` | `604800` (7 days) | How often the expensive orphan/storage tier runs; cheap metadata probes run every due cycle. |

### Scheduler

Gates and tunes the background scheduler that runs saved queries on their cron
[schedules](../guides/schedule-queries.md). Cron expressions are stored per
schedule (set in the UI), not here. Like the maintenance scanner the loop is
leader-elected via a Postgres advisory lock, so only one replica dispatches a given
due schedule — leave it enabled everywhere (see
[High availability](../deployment/high-availability.md)).

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Whether this replica participates in the (leader-elected) scheduler loop. Safe to leave `true` on every replica; set `false` only to exclude a replica entirely. |
| `SCHEDULER_TICK_S` | `60` | How often (seconds) the loop wakes to dispatch due schedules. Also the finest effective cadence — a schedule cannot run more often than one tick. |

### Catalog storage migration

Gates and tunes the background runner that performs
[catalog storage migrations](../guides/migrate-catalog-storage.md). Like the scheduler it is leader-elected via a
Postgres advisory lock, so only one replica advances a given migration — leave it enabled everywhere.

| Variable | Default | Description |
|---|---|---|
| `MIGRATION_RUNNER_ENABLED` | `true` | Whether this replica participates in the (leader-elected) migration runner. Safe to leave `true` on every replica; set `false` only to exclude a replica entirely. |
| `MIGRATION_RUNNER_TICK_S` | `30` | How often (seconds) the runner wakes to claim and advance an in-flight migration. |
| `MIGRATION_RETENTION_DAYS` | `7` | How long the old backend's data is retained after a successful cutover before the source Polaris catalog is dropped. The window during which a migration can be reversed. |

### SQL sessions

Gates and tunes the [SQL session layer](../concepts/sql-sessions.md) (persistent agent-held connections for external
warehouse-style clients). Disabled by default — enable it **only after** deploying the hardened agent, because turning
sessions on also enables the broader per-statement policy. The idle/max-lifetime reaper is leader-elected via a Postgres
advisory lock, like the scheduler, so leave it enabled everywhere.

| Variable | Default | Description |
|---|---|---|
| `SQL_SESSIONS_ENABLED` | `false` | Master switch for the session endpoints and the reaper. When `false`, the `/sql/sessions` routes return 404. |
| `SQL_SESSION_IDLE_TIMEOUT_S` | `900` | Close a session after this many seconds without a statement. Reset on each statement. |
| `SQL_SESSION_MAX_LIFETIME_S` | `14400` | Hard cap on a session's total lifetime, regardless of activity. Must exceed your longest single session's run. |
| `SQL_SESSION_REAPER_TICK_S` | `30` | How often (seconds) the reaper wakes to close idle/expired sessions. |
| `SQL_SESSION_OPEN_TIMEOUT_S` | `30` | How long the open endpoint waits for the agent to acknowledge the new session before returning a timeout. |
| `SQL_SESSION_STAGING_PREFIX_SEGMENT` | `_staging` | Object-storage path segment for a session's scoped staging area (`<catalog root>/<segment>/<session_id>/`); the statement policy confines `COPY`/`read_*` to this prefix. |
| `SQL_SESSION_STAGING_URL_TTL_S` | `900` | Lifetime (seconds) of the presigned `PUT`/`GET` staging URLs handed out by `POST /sql/sessions/{id}/staging-files`. Long enough to upload a load's files and run the `read_parquet` that consumes them. |
| `SQL_STATEMENT_ACK_DEADLINE_S` | `15` | Fail a session statement that stays `queued` past this many seconds — its dispatch frame never reached the agent. Only applied to agents that advertise ack support; see [SQL sessions](../concepts/sql-sessions.md#statement-delivery-and-deadlines). |
| `SQL_STATEMENT_TIMEOUT_GRACE_S` | `30` | Extra seconds beyond a statement's own `timeout_s` before the reaper fails a `running` statement whose agent reply never arrived. |
| `SQL_STATEMENT_DEFAULT_TIMEOUT_S` | `600` | Fallback timeout budget used by the reaper for statement rows with no recorded `timeout_s` (written before that column existed). |

### Elastic compute

Lets the control plane provision [agents](../concepts/agents.md) on demand instead of requiring them to be run by an
operator — see [Elastic compute](../concepts/elastic-compute.md) for the concept and
[Elastic compute on Azure](../deployment/azure-elastic-setup.md) for the cloud setup. Off by default and purely
additive: static agents behave the same whether or not this is enabled. The scale-in reaper is leader-elected via a
Postgres advisory lock, like the scheduler, so leave it enabled on every replica.

| Variable | Default | Description |
|---|---|---|
| `ELASTIC_COMPUTE_ENABLED` | `false` | Master switch. When `false`, the admin *New compute* action and elastic-pool query dispatch return 422 and no reaper runs. |
| `ELASTIC_PROVIDER` | `null` | Compute backend: `null` is a no-op used in tests, `azure_aci` provisions Azure Container Instances. |
| `ELASTIC_CONTROL_PLANE_URL` | — | The `wss://…/agents/connect` URL a provisioned agent dials home to. Required: unlike the interactive add-agent flow there is no HTTP request to derive it from. |
| `ELASTIC_AGENT_POLARIS_BASE_URL` | `POLARIS_BASE_URL` | Catalog endpoint provisioned agents attach against, when it differs from the control plane's own (a remote agent usually cannot use an in-cluster address). |
| `ELASTIC_IDLE_TIMEOUT_S` | `900` | Terminate an agent after this long with no work dispatched — and only when it has no in-flight queries or open SQL sessions. |
| `ELASTIC_MAX_LIFETIME_S` | `14400` | Hard lifetime backstop, applied once the agent's work drains. |
| `ELASTIC_PROVISIONING_DEADLINE_S` | `300` | Fail an agent that never dials home within this window and clean up its instance. |
| `ELASTIC_REAPER_TICK_S` | `30` | How often the scale-in and leak-reconciliation loop runs. |
| `ELASTIC_MAX_AGENTS_PER_POOL` | `1` | Cap on concurrent elastic agents per storage shape. A cost guardrail. |
| `ELASTIC_CURRENCY` | `USD` | Currency label shown next to the prices below. |

#### Azure Container Instances backend

Used when `ELASTIC_PROVIDER=azure_aci`. Credentials come from the ambient identity
(`DefaultAzureCredential`): a managed identity in Azure, or `AZURE_*` variables elsewhere.

| Variable | Default | Description |
|---|---|---|
| `ELASTIC_AZURE_SUBSCRIPTION_ID` | — | Subscription agent container groups are created in. Required. |
| `ELASTIC_AZURE_RESOURCE_GROUP` | — | Resource group they are created in. Required, and it must be dedicated: the reaper terminates every `duckhaven-managed` container group there that has no live agent row. |
| `ELASTIC_AZURE_SUBNET_ID` | — | Resource id of a subnet delegated to `Microsoft.ContainerInstance/containerGroups`. Required. Agents are injected into it with private addresses and no public DNS name, so the subnet needs its own outbound route and the control plane must be able to route to it. |
| `ELASTIC_AZURE_LOCATION` | `eastus` | Region the container groups are created in. |
| `ELASTIC_AZURE_CPU` | `2` | vCPU per agent for pool-triggered provisioning; the admin UI picks a size per agent. |
| `ELASTIC_AZURE_MEMORY_GB` | `8` | Memory (GiB) per agent, same. |
| `ELASTIC_AZURE_PRICE_VCPU_HOUR` | `0.0486` | Per-vCPU hourly rate, used only to show a size's cost in the admin UI. Despite the name it applies to **whichever provider is configured**; zero it on a Docker host, where the marginal hourly cost is nil. |
| `ELASTIC_AZURE_PRICE_MEMORY_GB_HOUR` | `0.0054` | Per-GiB hourly rate, same. |
| `ELASTIC_REGISTRY_SERVER` | — | Registry host for a private agent image. Leave unset for a public image. |
| `ELASTIC_REGISTRY_IDENTITY_ID` | — | Resource id of a user-assigned managed identity holding `AcrPull` on that registry. It is attached to each container group, which then pulls its image as itself, so no registry password exists. Container Instances supports user-assigned identities only for image pull, never system-assigned. |

#### Docker host

Used when `ELASTIC_PROVIDER=docker`, which provisions agents as containers on the single host
already running the stack. Enable it with `deploy/docker-compose.elastic.yml` and read
[Elastic compute on a single Docker host](../deployment/homelab-elastic-setup.md) first — giving the
control plane a path to the Docker daemon is the most privileged grant in the stack, and the socket
proxy narrows it without making container creation unprivileged.

| Variable | Default | Description |
|---|---|---|
| `ELASTIC_DOCKER_HOST` | `tcp://docker-socket-proxy:2375` | Where the daemon is reached. Point it at a `docker-socket-proxy` rather than a mounted socket, so the API container never holds the socket itself. |
| `ELASTIC_DOCKER_NETWORK` | `duckhaven_internal` | User-defined network agents are attached to. The bundled one is `internal: true`, which is what keeps an agent's result server reachable from the control plane and from nowhere off the host. |
| `ELASTIC_DOCKER_CPU` | `2` | vCPU per agent for pool-triggered provisioning; the admin UI picks a size per agent. |
| `ELASTIC_DOCKER_MEMORY_GB` | `4` | Memory (GiB) per agent, same. Becomes a real container memory limit, which the agent reads from its own cgroup to advertise capacity. |

Provisioned agents reproduce the static agent's sandbox — read-only root, `no-new-privileges`, all
capabilities dropped, a pids cap — so an agent you are given is contained exactly as tightly as one
you start by hand.

### AI assistant

Configures the governed [AI data assistant](../concepts/assistant.md). Disabled by default; enable it by pointing it at
a service account you created (see [Service accounts & tokens](../guides/service-accounts.md)) and configuring a model.
The provider is not fixed — use any of the bundled providers or an OpenAI-compatible endpoint. Provider API keys may be
supplied here or via the provider's own standard environment variable (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`MISTRAL_API_KEY`); a self-hosted OpenAI-compatible endpoint (Ollama, vLLM) can run keyless.

| Variable | Default | Description |
|---|---|---|
| `ASSISTANT_ENABLED` | `false` | Master switch. When `false`, the assistant API returns 503 and no model or provider dependency is exercised. |
| `ASSISTANT_SERVICE_ACCOUNT_SLUG` | — | Slug of the service account the assistant acts as. Its per-workspace membership and catalog grants govern its data access. Required when enabled. |
| `ASSISTANT_MODEL` | `anthropic:claude-sonnet-4-latest` | Pydantic AI model string, e.g. `openai:gpt-4o` or `mistral:mistral-large-latest`. For an OpenAI-compatible endpoint, use `openai:<model>` with the base URL below. |
| `ASSISTANT_OPENAI_BASE_URL` | — | Base URL of an OpenAI-compatible endpoint (Ollama, vLLM, Azure). When set, the model routes through the OpenAI protocol against this endpoint. |
| `ASSISTANT_API_KEY` | — | Explicit API key for the model. Optional: hosted providers fall back to their standard env var; keyless endpoints need nothing. **Rotate like any secret.** |
| `ASSISTANT_PAT_TTL_S` | `3600` | Lifetime (seconds) of the short-lived PAT minted for each turn's loopback calls. Must exceed the longest plausible turn (bounded by `ASSISTANT_REQUEST_LIMIT` × per-query timeout), since one token is reused for the whole turn. |
| `ASSISTANT_MAX_CONCURRENCY` | `4` | Max concurrent assistant runs per API process, bounding how much the shared event loop can be occupied by model turns. |
| `ASSISTANT_REQUEST_LIMIT` | `20` | Hard cap on model requests per turn — stops a tool-loop from running queries and burning tokens indefinitely. |
| `ASSISTANT_MAX_OUTPUT_TOKENS` | `4096` | Cap on model output tokens per turn — a coarse cost guard. |
| `ASSISTANT_HISTORY_TURN_CAP` | `40` | Only the most recent N turns are replayed to the model, bounding per-turn cost and context growth. |
| `ASSISTANT_RESULT_ROW_CAP` | `100` | Max rows of a query result fed into model context (the full result is still available in the UI). |
| `ASSISTANT_RESULT_BYTE_CAP` | `32768` | Max bytes of a result sample fed into model context. |
| `ASSISTANT_TRACE_INCLUDE_CONTENT` | `true` | When [tracing](../operations/tracing.md#the-ai-assistant) is enabled, record the turn's content (prompt, generated SQL, tool arguments, result samples) on spans. Set `false` to keep only structure — roles, token usage, tool names, timing, status — out of the trace backend. No effect when tracing is off. |

### Observability

Controls the Prometheus metrics endpoint and OpenTelemetry tracing. See
[Monitoring](../operations/monitoring.md#prometheus-metrics) for the metric reference, scrape config, and a starter
Grafana dashboard, and [Distributed tracing](../operations/tracing.md) for the tracing pipeline.

| Variable | Default | Description |
|---|---|---|
| `METRICS_ENABLED` | `true` | Expose `GET /api/metrics` in Prometheus text format. Unauthenticated like the health endpoints (keep it on the internal network); set `false` to remove the endpoint entirely. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP (http/protobuf) endpoint to export traces to; the compose files default it to the bundled collector, `http://otel-collector:4318`. Unset/empty disables tracing entirely. |
| `OTEL_SERVICE_NAME` | `duckhaven-api` | Service name reported on spans. Replicas are distinguished by `REPLICA_ID` as `service.instance.id`. |

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
| `SESSION_RESERVATION_BYTES` | No | `268435456` (256 MB) | Memory a held [SQL session](../concepts/sql-sessions.md) reserves for its lifetime (fixes the connection's `memory_limit`), clamped to the agent's budget. |
| `SANDBOX_DISABLED_FILESYSTEMS` | No | — | DuckDB `disabled_filesystems` applied to every connection (comma/space separated; unknown names are logged and skipped). Off by default because the agent reads [staged files](../concepts/sql-sessions.md#staging-files-presigned-urls) over presigned HTTP(S) URLs, which `HTTPFileSystem` serves — set `HTTPFileSystem` only on a deployment that does not use staging. It does **not** break the bundled Polaris/MinIO: the Iceberg REST client and the S3 filesystem are independent of the generic HTTP one. |
| `SANDBOX_LOCK_CONFIGURATION` | No | `true` | Lock DuckDB's configuration once the agent has finished setting a connection up, so a session statement cannot re-widen the sandbox with `SET` (`disabled_filesystems`, `enable_external_access`, `secret_directory`, `extension_directory`, `home_directory`, `custom_extension_repository`, `allow_unsigned_extensions`, and the lock itself). The agent's own per-statement settings stay writable. Set `false` only to diagnose a suspected interaction. See [Sandboxing](../concepts/sql-sessions.md#sandboxing). |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | — | OTLP (http/protobuf) endpoint to export traces to; the compose files default it to the bundled collector. Unset/empty disables tracing entirely. See [Distributed tracing](../operations/tracing.md). |
| `OTEL_SERVICE_NAME` | No | `duckhaven-agent` | Service name reported on spans. |

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
