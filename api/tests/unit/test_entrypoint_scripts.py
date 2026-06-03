"""Tests for the deploy entrypoint (api-entrypoint.sh).

The entrypoint folds in the former init-secrets one-shot: it generates the app
secret on first boot, then reads it and constructs DATABASE_URL before exec'ing
the command. It reads SECRETS_DIR / DATA_DIR from the env, so tests can point it
at a tempdir without /var/duckhaven needing to exist. We invoke it via `sh` and
assert filesystem + exported-env effects.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "deploy" / "api-entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not available")


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **env}
    return subprocess.run(
        ["sh", str(ENTRYPOINT), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


def _dirs(tmp_path: Path) -> dict[str, str]:
    return {"SECRETS_DIR": str(tmp_path / "secrets"), "DATA_DIR": str(tmp_path)}


def test_generates_secret_key_and_setup_token_on_first_boot(tmp_path: Path) -> None:
    result = _run(_dirs(tmp_path), "true")
    assert result.returncode == 0, result.stderr

    secret_key = tmp_path / "secrets" / "secret_key"
    setup_token = tmp_path / "setup_token"
    assert secret_key.is_file() and setup_token.is_file()
    # Non-empty, no trailing newline; random base64 of 32 bytes is ~44 chars.
    assert secret_key.read_text() and "\n" not in secret_key.read_text()
    assert len(secret_key.read_text()) >= 40
    assert setup_token.read_text()


def test_secret_key_is_idempotent(tmp_path: Path) -> None:
    env = _dirs(tmp_path)
    _run(env, "true")
    first = (tmp_path / "secrets" / "secret_key").read_text()
    _run(env, "true")
    second = (tmp_path / "secrets" / "secret_key").read_text()
    assert first == second, "second run must not regenerate the existing secret"


def test_honours_secret_key_override_on_first_boot(tmp_path: Path) -> None:
    result = _run({**_dirs(tmp_path), "SECRET_KEY": "operator-supplied-key"}, "true")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "secrets" / "secret_key").read_text() == "operator-supplied-key"


def test_secret_key_override_ignored_after_first_boot(tmp_path: Path) -> None:
    _run({**_dirs(tmp_path), "SECRET_KEY": "first"}, "true")
    _run({**_dirs(tmp_path), "SECRET_KEY": "second"}, "true")
    assert (tmp_path / "secrets" / "secret_key").read_text() == "first"


def test_does_not_regenerate_setup_token_after_consumed(tmp_path: Path) -> None:
    env = _dirs(tmp_path)
    # First boot: token generated.
    _run(env, "true")
    # API consumed the token after first-admin creation.
    (tmp_path / "setup_token").unlink()
    # Subsequent boot must NOT mint a new one — otherwise a stranger reading
    # the volume could create a second admin.
    _run(env, "true")
    assert not (tmp_path / "setup_token").exists()


def test_exports_secret_key_and_database_url(tmp_path: Path) -> None:
    result = _run(
        {**_dirs(tmp_path), "SECRET_KEY": "test-secret", "POSTGRES_PASSWORD": "pg-test-pw"},
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


def test_database_url_defaults_password_when_unset(tmp_path: Path) -> None:
    # Empty POSTGRES_PASSWORD falls back to the shared default.
    result = _run(
        {**_dirs(tmp_path), "POSTGRES_PASSWORD": ""},
        "sh",
        "-c",
        "echo $DATABASE_URL",
    )
    assert result.returncode == 0, result.stderr
    assert "postgresql+asyncpg://duckhaven:duckhaven@postgres:5432/duckhaven" in result.stdout


def test_respects_postgres_overrides(tmp_path: Path) -> None:
    result = _run(
        {
            **_dirs(tmp_path),
            "POSTGRES_PASSWORD": "pw",
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
