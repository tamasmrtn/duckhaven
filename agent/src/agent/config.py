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

    # Tier-2 cost estimation (the `auto` profile). Each query's reservation is
    # sized from its EXPLAIN estimate * safety, snapped to a T-shirt bucket, and
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


settings = Settings()
