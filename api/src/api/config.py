from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://duckhaven:duckhaven@localhost:5432/duckhaven"
    uc_base_url: str = "http://localhost:8080"
    secret_key: str = "change-me-in-production"
    session_max_age_seconds: int = 86400 * 7
    cors_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = True


settings = Settings()
