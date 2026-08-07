"""Runtime configuration: secrets from the environment (via .env), structure
from the YAML files in config/.

Secrets never live in the YAML files — those hold scale factors, scenarios,
and sizing tiers, and are safe to commit and publish alongside
METHODOLOGY.md. .env is gitignored.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DuckHaven
    duckhaven_base_url: str = ""
    duckhaven_workspace: str = "tpch-bench"
    duckhaven_pat: str = ""

    # Snowflake
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_role: str = ""

    # Databricks
    databricks_host: str = ""
    databricks_client_id: str = ""
    databricks_client_secret: str = ""
    databricks_warehouse_id: str = ""

    # Corpus storage
    corpus_azure_connection_string: str = ""
    corpus_azure_container: str = "tpch-corpus"
    corpus_s3_bucket: str = ""
    corpus_aws_access_key_id: str = ""
    corpus_aws_secret_access_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _load_yaml(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open() as f:
        return yaml.safe_load(f)


@lru_cache
def engines_config() -> dict[str, Any]:
    return _load_yaml("engines.yaml")


@lru_cache
def scale_factors_config() -> dict[str, Any]:
    return _load_yaml("scale_factors.yaml")


@lru_cache
def scenarios_config() -> dict[str, Any]:
    return _load_yaml("scenarios.yaml")


@lru_cache
def sizing_matrix_config() -> dict[str, Any]:
    return _load_yaml("sizing_matrix.yaml")
