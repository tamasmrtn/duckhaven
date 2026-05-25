from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    control_plane_url: str = "ws://localhost:8000/agents/connect"
    bootstrap_token: str = ""
    results_dir: str = "/var/duckhaven-agent/results"
    results_http_port: int = 8001
    memory_limit_bytes: int = 6 * 1024**3  # 6 GB default
    # Operator-set, non-overridable ceilings: per-query overrides clamp to these.
    max_memory_limit_gb: float = 6.0
    max_timeout_s: float = 600.0
    result_retention_hours: float = 24.0
    retention_sweep_interval_s: float = 3600.0


settings = Settings()
