from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    control_plane_url: str = "ws://localhost:8000/agents/connect"
    bootstrap_token: str = ""
    # Operator-assigned display name, set once at first registration. Empty =>
    # falls back to the host name. Ignored on reconnect (name is bound at signup).
    agent_name: str = ""
    # Apache Polaris (Iceberg REST catalog) the agent's DuckDB ATTACHes per
    # query. DuckDB performs the OAuth2 client-credentials exchange itself using
    # these; storage creds are then vended by Polaris on attach. Dev defaults
    # match the Polaris bootstrap root principal.
    polaris_base_url: str = "http://localhost:8181"
    polaris_client_id: str = "root"
    polaris_client_secret: str = "s3cr3t"
    results_dir: str = "/var/duckhaven-agent/results"
    # File where the agent persists its long-lived session token so it can
    # re-authenticate across restarts instead of re-consuming the single-use
    # bootstrap token. Empty => resolved at runtime to `<results_dir>/.session-token`
    # (the results dir is the agent's persistent volume; the retention sweep only
    # touches `*.parquet`, so a dotfile here is safe).
    session_token_path: str = ""
    # Interface the result server binds. Defaults to all interfaces because the
    # control plane reaches the result port across a container/host boundary in the
    # remote-agent topology; the endpoint is Bearer-gated by the session token.
    results_http_host: str = "0.0.0.0"  # noqa: S104 - intentional; Bearer-gated endpoint
    results_http_port: int = 8001
    # Address the control plane should use to reach this agent's result server.
    # Normally the API derives it from the socket peer (works when the agent's
    # inbound and outbound addresses match). Set this when they differ — e.g. an
    # Azure Container Instances agent whose result server is reached via a public
    # DNS label distinct from its egress IP. Empty => fall back to the peer address.
    result_advertise_host: str = ""
    # Operator-set, non-overridable ceiling: per-query timeout overrides clamp to this.
    max_timeout_s: float = 600.0
    result_retention_hours: float = 24.0
    retention_sweep_interval_s: float = 3600.0
    # Cadence at which the agent pushes live CPU/memory utilization samples over
    # the control channel. Independent of (and finer than) capability heartbeats.
    metrics_sample_interval_s: float = 2.0
    # Query admission / queueing (see agent.executor.admission). The concurrency
    # profile is the default slot ladder; users can switch it at runtime with the
    # worksheet `SET duckhaven_concurrency` command. Headroom is the fraction of
    # the cgroup/host memory held back so DuckDB overshoot can't trip the OOM
    # killer. queued_timeout_s = 0 disables the queued timeout.
    max_concurrency_profile: str = "auto"
    memory_headroom_fraction: float = 0.10
    max_queue_depth: int = 100
    queued_timeout_s: float = 0.0
    # What a held SQL session reserves while it is idle: enough for the attached
    # connection's own footprint, not for the heaviest query it might ever run.
    # Under `auto` each statement grows the reservation to its own estimate and
    # shrinks back here afterwards, so this is a floor, not a ceiling. Keeping it
    # small is what leaves headroom to grow into: at the old flat 256 MiB, 14 open
    # sessions committed a 4 GB agent's entire budget and no statement could ever
    # grow. Clamped to the agent's budget.
    session_baseline_bytes: int = 64 * 1024 * 1024
    # Ceiling on how far one session statement may grow, as a fraction of the
    # agent's budget. 1.0 lets a single heavy statement use the whole agent when
    # nothing else is running; lower it to keep more in reserve for other tenants.
    session_max_bucket_fraction: float = 1.0
    # How long a session open may sit in the admission queue before it is failed.
    # Unlike a query (``queued_timeout_s = 0``, wait indefinitely), an open races
    # the control plane's own ``SQL_SESSION_OPENING_DEADLINE_S``: waiting past it
    # cannot succeed, it only replaces a prompt error with a two-minute hang. Kept
    # under that deadline so the agent is the one that reports the failure; <= 0
    # restores the old wait-forever behaviour.
    session_queued_timeout_s: float = 30.0
    # Agent-owned session lease (the backstop under the API reaper). The agent
    # self-expires a held session idle past ``session_idle_timeout_s`` or older than
    # ``session_max_lifetime_s``, freeing its connection + admission slot even if a
    # CLOSE_SESSION was lost. Deliberately LARGER than the API defaults (900 / 14400)
    # so the control-plane reaper stays primary and the agent only reclaims true
    # orphans; <= 0 disables the respective check.
    session_idle_timeout_s: float = 1200.0
    session_max_lifetime_s: float = 18000.0

    # EXPLAIN-based cost estimation (the `auto` profile). Each query's reservation
    # is sized from its EXPLAIN estimate * safety, snapped to a T-shirt bucket, and
    # clamped to [floor, ceiling*budget]. Unestimable queries (DDL/DML,
    # multi-statement, EXPLAIN failure, timeout) fall back to `estimate_fallback_bucket`.
    estimate_safety_multiplier: float = 1.5
    estimate_floor_bytes: int = 64 * 1024 * 1024
    estimate_ceiling_fraction: float = 1.0
    # Ceiling on the pre-run `EXPLAIN`, enforced with `conn.interrupt()`, on BOTH
    # estimate paths (one-shot dispatch and session statements). Not a nicety:
    # DuckDB's planner can spin inside EXPLAIN itself on a heavy join order —
    # observed on TPC-H Q08 against SF10, pinning a core with the statement never
    # starting. The statement's own timeout does not cover this, because execution
    # has not begun. An interrupted EXPLAIN is simply unestimable, so the query
    # falls back to `estimate_fallback_bucket`. Q08's EXPLAIN normally takes ~90 ms
    # at SF10, so 2s is a wide margin, not a tight budget.
    explain_timeout_s: float = 2.0
    estimate_fallback_bucket: str = "M"

    # How far the revocable "elastic" cache grant may top a statement up, as a
    # fraction of the agent's budget. A statement is grown to this fraction *if*
    # its required reservation is smaller (a required reservation already above
    # it is left alone — that tier has its own `estimate_ceiling_fraction`).
    #
    # This is what lets an otherwise-idle agent give a scan-heavy query enough
    # memory to keep DuckDB's EXTERNAL_FILE_CACHE warm instead of re-reading its
    # Parquet from object storage on every pass. Below 1.0 on purpose, and
    # independent of `memory_headroom_fraction`: `memory_limit` bounds DuckDB's
    # own allocations, not the process, so the agent needs room for Python, Arrow
    # buffers and the httpfs/iceberg extensions above what DuckDB may use. Lower
    # it to keep more in reserve for other tenants; the grant is revocable either
    # way, so an idle session holding cache never blocks anyone.
    elastic_ceiling_fraction: float = 0.85

    # A statement that cannot grow to a workable share of its estimate waits for
    # budget rather than running into a spill storm. On a saturated agent this is
    # what turns a thundering herd into a staggered one: 22 simultaneous SF10
    # queries at the 64 MiB idle baseline spilled hard enough to take the agent
    # process down, where waiting their turn lets each run at a size it can
    # actually use. The wait is additionally bounded by the statement's own
    # timeout, so it can never outlive the query it is sizing.
    #
    # Deliberately long: waiting is the stability mechanism, not a hiccup. Set to
    # 0 to restore the old never-block behaviour. `admission_wait_ms` in the query
    # profile reports what each statement actually spent here.
    statement_admission_wait_s: float = 300.0
    # Wait only while the granted reservation is below this fraction of what the
    # statement asked for. 1.0 waits for the full estimate; 0 never waits.
    statement_admission_floor_fraction: float = 0.5

    # Post-execution profiling: capture DuckDB's JSON profile per query and ship
    # it in QUERY_DONE. Best-effort; this flag is the kill switch.
    profiling_enabled: bool = True

    # DuckDB filesystem sandbox (defense-in-depth beneath the API statement
    # policy). A DuckDB `disabled_filesystems` value applied to every connection,
    # comma/space-separated; unknown names are warned about and skipped (DuckDB
    # accepts any string silently, so a typo would otherwise disable nothing).
    #
    # DEFAULT EMPTY / OFF, but NOT for the reason previously recorded here.
    # Re-verified empirically on DuckDB 1.5.4 against the bundled stack:
    #   - Disabling HTTPFileSystem does NOT break plain-HTTP Polaris or MinIO. The
    #     iceberg REST client and S3FileSystem are independent of HTTPFileSystem:
    #     ATTACH over `http://polaris:8181` and SELECT from an Iceberg table on
    #     MinIO both still work. (The older comment claiming otherwise was wrong.)
    #   - It DOES break presigned-URL staging (#160/#169): the agent reads staged
    #     files via `read_parquet('http(s)://…?X-Amz-…')`, which is HTTPFileSystem.
    #     That is the real reason this ships off — a shipped feature depends on it.
    #   - `COPY … TO 'http://…'` is not a vector at all: DuckDB answers "Writing to
    #     HTTP files not implemented". The HTTP risk is READ, and it is contained by
    #     the agent's network egress restriction (see deploy/docker-compose.yml).
    #   - `allowed_directories`/`allowed_paths` are NOT enforced while
    #     `enable_external_access` is on (which the agent requires for S3/Polaris),
    #     and cannot be set before the database starts; `LocalFileSystem` cannot be
    #     disabled without breaking result-Parquet materialization. Local-FS
    #     containment is therefore carried by the read-only container rootfs + the
    #     API statement policy.
    # Set "HTTPFileSystem" on a deployment that does not use presigned staging.
    sandbox_disabled_filesystems: str = ""

    # Lock DuckDB's configuration (`allowed_configs` + `lock_configuration`) once
    # the agent has finished setting a connection up. This is what stops a session
    # statement re-widening the sandbox with `SET` — verified on 1.5.4 to block
    # `disabled_filesystems`, `enable_external_access`, `secret_directory`,
    # `extension_directory`, `home_directory`, `custom_extension_repository`,
    # `allow_unsigned_extensions`, and both `lock_configuration` and
    # `allowed_configs` themselves, while leaving the runner's own needs writable
    # (see `_ALLOWED_CONFIGS` in executor/runner.py). On by default; the kill
    # switch exists because it is applied to every connection.
    sandbox_lock_configuration: bool = True

    # OpenTelemetry tracing. Unset endpoint (the default) disables the SDK
    # entirely — no spans, no exporter. Maps from the standard
    # OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_SERVICE_NAME env vars.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "duckhaven-agent"


settings = Settings()
