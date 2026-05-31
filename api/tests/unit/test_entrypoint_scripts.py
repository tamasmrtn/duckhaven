"""Tests for the deploy shell scripts (init-secrets.sh, api-entrypoint.sh).

The scripts read SECRETS_DIR from the env, so tests can point them at a
tempdir without /var/duckhaven needing to exist. We invoke the scripts via
`sh` and assert filesystem + exported-env effects.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INIT_SCRIPT = REPO_ROOT / "deploy" / "init-secrets.sh"
ENTRYPOINT = REPO_ROOT / "deploy" / "api-entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not available")


def _run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **env}
    return subprocess.run(
        ["sh", str(script), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


def test_init_secrets_generates_both_files(tmp_path: Path) -> None:
    result = _run(INIT_SCRIPT, {"SECRETS_DIR": str(tmp_path), "DATA_DIR": str(tmp_path)})
    assert result.returncode == 0, result.stderr

    secret_key = tmp_path / "secret_key"
    pg_password = tmp_path / "postgres_password"
    assert secret_key.is_file() and pg_password.is_file()
    # Non-empty, no trailing newline.
    assert secret_key.read_text() and "\n" not in secret_key.read_text()
    assert pg_password.read_text() and "\n" not in pg_password.read_text()
    # Random base64 of 32 bytes is ~44 chars.
    assert len(secret_key.read_text()) >= 40
    assert len(pg_password.read_text()) >= 40


def test_init_secrets_is_idempotent(tmp_path: Path) -> None:
    env = {"SECRETS_DIR": str(tmp_path), "DATA_DIR": str(tmp_path)}
    _run(INIT_SCRIPT, env)
    first = (tmp_path / "secret_key").read_text()
    _run(INIT_SCRIPT, env)
    second = (tmp_path / "secret_key").read_text()
    assert first == second, "second run must not regenerate existing secrets"


def test_init_secrets_honours_env_overrides_on_first_boot(tmp_path: Path) -> None:
    result = _run(
        INIT_SCRIPT,
        {
            "SECRETS_DIR": str(tmp_path),
            "DATA_DIR": str(tmp_path),
            "SECRET_KEY": "operator-supplied-key",
            "POSTGRES_PASSWORD": "operator-supplied-pw",
        },
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "secret_key").read_text() == "operator-supplied-key"
    assert (tmp_path / "postgres_password").read_text() == "operator-supplied-pw"


def test_init_secrets_env_override_ignored_after_first_boot(tmp_path: Path) -> None:
    _run(
        INIT_SCRIPT,
        {"SECRETS_DIR": str(tmp_path), "DATA_DIR": str(tmp_path), "SECRET_KEY": "first"},
    )
    _run(
        INIT_SCRIPT,
        {"SECRETS_DIR": str(tmp_path), "DATA_DIR": str(tmp_path), "SECRET_KEY": "second"},
    )
    assert (tmp_path / "secret_key").read_text() == "first"


def test_init_secrets_generates_setup_token_on_first_boot(tmp_path: Path) -> None:
    _run(INIT_SCRIPT, {"SECRETS_DIR": str(tmp_path), "DATA_DIR": str(tmp_path)})
    setup_token = tmp_path / "setup_token"
    assert setup_token.is_file() and setup_token.read_text()


def test_init_secrets_does_not_regenerate_setup_token_after_consumed(tmp_path: Path) -> None:
    env = {"SECRETS_DIR": str(tmp_path), "DATA_DIR": str(tmp_path)}
    # First boot: token generated.
    _run(INIT_SCRIPT, env)
    # API consumed the token after first-admin creation.
    (tmp_path / "setup_token").unlink()
    # Subsequent boot must NOT mint a new one — otherwise a stranger reading
    # the volume could create a second admin.
    _run(INIT_SCRIPT, env)
    assert not (tmp_path / "setup_token").exists()


def test_api_entrypoint_exports_secret_key_and_database_url(tmp_path: Path) -> None:
    (tmp_path / "secret_key").write_text("test-secret")
    (tmp_path / "postgres_password").write_text("pg-test-pw")

    result = _run(
        ENTRYPOINT,
        {"SECRETS_DIR": str(tmp_path)},
        "sh",
        "-c",
        "echo SECRET_KEY=$SECRET_KEY; echo DATABASE_URL=$DATABASE_URL",
    )
    assert result.returncode == 0, result.stderr
    assert "SECRET_KEY=test-secret" in result.stdout
    assert (
        "DATABASE_URL=postgresql+asyncpg://duckhaven:pg-test-pw@postgres:5432/duckhaven"
        in result.stdout
    )


def test_api_entrypoint_respects_postgres_overrides(tmp_path: Path) -> None:
    (tmp_path / "secret_key").write_text("k")
    (tmp_path / "postgres_password").write_text("pw")

    result = _run(
        ENTRYPOINT,
        {
            "SECRETS_DIR": str(tmp_path),
            "POSTGRES_USER": "alt_user",
            "POSTGRES_HOST": "altdb",
            "POSTGRES_PORT": "6543",
            "POSTGRES_DB": "alt_db",
        },
        "sh",
        "-c",
        "echo $DATABASE_URL",
    )
    assert result.returncode == 0, result.stderr
    assert "postgresql+asyncpg://alt_user:pw@altdb:6543/alt_db" in result.stdout


def test_api_entrypoint_fails_loud_on_missing_secret(tmp_path: Path) -> None:
    # Only one of the two secrets present.
    (tmp_path / "secret_key").write_text("k")

    result = _run(ENTRYPOINT, {"SECRETS_DIR": str(tmp_path)}, "true")
    assert result.returncode != 0
    assert "missing secret file" in result.stderr
