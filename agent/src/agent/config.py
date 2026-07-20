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
    # Memory a held SQL session reserves for its whole lifetime (occupying an
    # admission slot so long-lived sessions can't oversubscribe the budget or
    # starve interactive queries). Fixes the session connection's `memory_limit`
    # at open (DuckDB has no live resize), clamped to the agent's budget.
    session_reservation_bytes: int = 256 * 1024 * 1024
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
    explain_timeout_s: float = 2.0
    estimate_fallback_bucket: str = "M"

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
