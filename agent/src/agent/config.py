from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    control_plane_url: str = "ws://localhost:8000/agents/connect"
    bootstrap_token: str = ""
    results_dir: str = "/var/duckhaven-agent/results"
    results_http_port: int = 8001
    memory_limit_bytes: int = 6 * 1024**3  # 6 GB default


settings = Settings()
