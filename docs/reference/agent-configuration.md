---
type: reference
---

# Agent configuration

Every environment variable an agent reads; an agent needs only `CONTROL_PLANE_URL` and `BOOTSTRAP_TOKEN`.

The rest have working defaults. Set them in the agent's environment, such as the `environment:` block of its compose
snippet, and restart the agent. The control plane's own settings are in [Configuration](configuration.md). Each table's
first column is checked by a unit test against the agent's settings, so the list is complete.

## Connection

| Variable | Default | Meaning |
|---|---|---|
| `CONTROL_PLANE_URL` | `ws://localhost:8000/agents/connect` | The control plane's agent endpoint. Use `wss://` behind TLS. **Required** |
| `BOOTSTRAP_TOKEN` | – | The one-time token from **Compute → Generate bootstrap**, valid for 24 hours. **Required** on the first start |
| `AGENT_NAME` | the host name | Display name, set at first registration and kept after |
| `SESSION_TOKEN_PATH` | `⟨RESULTS_DIR⟩/.session-token` | Where the agent keeps the long-lived credential it gets for the bootstrap token, so it reconnects after a restart without a new token |
| `POLARIS_BASE_URL` | `http://localhost:8181` | Fallback Polaris address, used only if the control plane sends none with a query; current control planes always send one |
| `POLARIS_CLIENT_ID` | `root` | Fallback Polaris client id, as above |
| `POLARIS_CLIENT_SECRET` | `s3cr3t` | Fallback Polaris client secret, as above |

## Results

| Variable | Default | Meaning |
|---|---|---|
| `RESULTS_DIR` | `/var/duckhaven-agent/results` | Where result files are written. Give each agent its own, on a persistent volume |
| `RESULTS_HTTP_HOST` | `0.0.0.0` | Interface the result server listens on. It answers only requests that carry the agent's session credential |
| `RESULTS_HTTP_PORT` | `8001` | Port the control plane reads results from |
| `RESULT_ADVERTISE_HOST` | the connection's source address | The address the control plane uses to reach the result server. Set it when the agent is reached at a different address than it connects from, such as a public DNS name |
| `RESULT_RETENTION_HOURS` | `24` | How long result files are kept. A file the [result cache](../querying/result-cache.md) serves is kept longer, until its entry expires |
| `RETENTION_SWEEP_INTERVAL_S` | `3600` | How often expired result files are removed |
| `RESULT_CACHE_MAX_BYTES` | `10737418240` (10 GiB) | Ceiling on result files kept past their retention for the result cache. Past it, the oldest go first and the cache misses for them |
| `MAX_TIMEOUT_S` | `600` | Upper bound on any query's timeout; longer requested timeouts are cut to it |

## Admission and concurrency

The agent reads its memory budget from its container's cgroup (`memory.max`), or the host's memory, less a headroom.
There is no setting for the budget itself: size the container and the agent follows. See
[Query execution](../querying/query-execution.md#admission-control-and-concurrency).

| Variable | Default | Meaning |
|---|---|---|
| `MAX_CONCURRENCY_PROFILE` | `auto` | How queries share memory: `auto` sizes each from its plan; `single`, `equal_2`, `decaying_2` and `decaying_3` are fixed slot ladders. Can be changed while the agent runs with the [concurrency command](sql-support.md#concurrency-control-command) |
| `MEMORY_HEADROOM_FRACTION` | `0.10` | Share of memory held back from queries, so DuckDB's overshoot cannot trigger the out-of-memory killer |
| `MAX_QUEUE_DEPTH` | `100` | Queries that may wait; one more fails with `queue full` |
| `QUEUED_TIMEOUT_S` | `0` (off) | Fail a query that waits longer than this with `queued timeout` |
| `STATEMENT_ADMISSION_WAIT_S` | `300` | How long a statement may wait for memory before running with what it can get; also bounded by its timeout. `0` never waits |
| `STATEMENT_ADMISSION_FLOOR_FRACTION` | `0.5` | Wait only while the memory available is below this share of the request. `1.0` waits for all of it; `0` never waits |
| `ELASTIC_CEILING_FRACTION` | `0.85` | How far idle memory may top up a statement beyond its reservation, for DuckDB's cache of the Parquet it reads. Taken back as soon as another query needs it |
| `WARM_CONNECTIONS` | `2` | Connections kept open with their extensions loaded, for each of the last four mixes of catalog and storage kinds the agent has served, so a query or session open skips loading them. That takes about 0.3 s off a query on a DuckLake workspace and 0.1 to 0.35 s on an Iceberg one; most of it is DuckDB checking each extension's signature. Each is used once, then replaced in the background on one CPU, and holds a few MB while it waits. Queries that arrive faster than the replacements, such as a burst of dashboard tiles, open the rest when they are needed. `0` opens every connection when it is needed |

### The `auto` estimator

Under `auto`, the agent runs `EXPLAIN` before each query and reserves memory from the plan's estimate.

| Variable | Default | Meaning |
|---|---|---|
| `ESTIMATE_SAFETY_MULTIPLIER` | `1.5` | Multiplies the plan's estimate, to absorb underestimates |
| `ESTIMATE_FLOOR_BYTES` | `67108864` (64 MiB) | Smallest reservation |
| `ESTIMATE_CEILING_FRACTION` | `1.0` | Largest reservation, as a share of the budget |
| `EXPLAIN_TIMEOUT_S` | `2.0` | Time allowed for the `EXPLAIN`; a slower plan uses the fallback size |
| `ESTIMATE_FALLBACK_BUCKET` | `M` | Size for a query that cannot be estimated: DDL, DML, several statements, or a failed `EXPLAIN` |
| `ESTIMATE_CACHE_TTL_S` | `300` | How long an estimate is reused for the same SQL and catalogs |
| `ESTIMATE_CACHE_MAX_ENTRIES` | `512` | How many estimates are kept |
| `GRANT_FEEDBACK_ENABLED` | `true` | Size a repeated query from the memory it used last time instead of its plan, which overshoots real peaks by 5 to 11 times. Any write drops what was learned. See [Learning from what a query actually used](../querying/query-execution.md#learning-from-what-a-query-actually-used) |
| `GRANT_FEEDBACK_SAFETY_MULTIPLIER` | `1.5` | Margin on a remembered peak, because data grows between runs |

### SQL sessions

| Variable | Default | Meaning |
|---|---|---|
| `SESSION_BASELINE_BYTES` | `67108864` (64 MiB) | Memory a [SQL session](../querying/sql-sessions.md) holds between statements. Each statement grows it to its own estimate and shrinks back. It sets how many sessions an agent holds |
| `SESSION_MAX_BUCKET_FRACTION` | `0.333` | Largest share of the budget one statement may require at admission, so at least three can run at once. It does not limit what a statement uses on an idle agent |
| `SESSION_QUEUED_TIMEOUT_S` | `30` | How long a session open may wait for capacity. Keep it below the control plane's `SQL_SESSION_OPENING_DEADLINE_S` |
| `SESSION_IDLE_TIMEOUT_S` | `1200` | The agent closes a session idle this long, in case the control plane's close was lost. Longer than the control plane's own timeout on purpose. `0` turns the check off |
| `SESSION_MAX_LIFETIME_S` | `18000` | The agent closes a session older than this, on the same terms |
| `SESSION_RESULT_INLINE_MAX_BYTES` | `262144` (256 KiB) | A statement result file no larger than this goes to the control plane with the statement's completion, so the response doesn't wait for the control plane to fetch it back. The file stays on the agent either way. `0` turns this off |

## Sandbox

| Variable | Default | Meaning |
|---|---|---|
| `SANDBOX_LOCK_CONFIGURATION` | `true` | Lock DuckDB's configuration after a connection is set up, so a statement cannot widen filesystem access, extension loading or secret storage with `SET`. Turn off only to diagnose a problem. See [Sandboxing](../querying/sql-sessions.md#sandboxing) |
| `SANDBOX_DISABLED_FILESYSTEMS` | – | DuckDB filesystems to turn off on every connection, separated by commas or spaces. `HTTPFileSystem` breaks [staged files](../querying/sql-sessions.md#staging-files), so set it only where staging is not used |

## Observability

| Variable | Default | Meaning |
|---|---|---|
| `PROFILING_ENABLED` | `true` | Record each query's DuckDB profile, which [query profiles](../querying/query-profiles.md) and the monitoring page's per-query memory, CPU and spill come from |
| `METRICS_SAMPLE_INTERVAL_S` | `2.0` | How often the agent reports CPU, memory and queue figures |
| `METRICS_MEMORY_POLL_INTERVAL_S` | `0.25` | How often, between samples, it re-reads memory to catch a short spike. `0` turns the poll off |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | – | OTLP (HTTP/protobuf) endpoint for traces; unset turns tracing off. The compose files point it at the bundled collector. See [Distributed tracing](../operations/tracing.md) |
| `OTEL_SERVICE_NAME` | `duckhaven-agent` | Service name on the agent's spans |

## Related

- [Add an agent](../compute/add-agent.md) — the compose snippet these go into.
- [Agents](../compute/agents.md) — extensions and runtimes.
- [Configuration](configuration.md) — the control plane's settings.
