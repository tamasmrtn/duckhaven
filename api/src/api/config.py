from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://duckhaven:duckhaven@localhost:5432/duckhaven"
    uc_base_url: str = "http://localhost:8080"
    uc_token: str | None = None
    uc_http_timeout_s: float = 10.0
    # Refresh vended storage creds once their remaining lifetime drops below
    # this many seconds. Default sized for UC OSS's ~hour TTL (half-TTL).
    cred_safety_window_s: int = 1800
    secret_key: str = "change-me-in-production"
    session_max_age_seconds: int = 86400 * 7
    cors_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = True
    # Directory of the built SPA, served at / when present (baked into the image).
    static_dir: Path = Path("/app/static")


settings = Settings()
