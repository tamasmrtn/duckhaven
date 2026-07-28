"""Tests for the deploy entrypoint (api-entrypoint.sh).

The entrypoint folds in the former init-secrets one-shot: it generates the app
secret on first boot, then reads it and — unless the environment already supplies
one — constructs DATABASE_URL before exec'ing the command. It reads
SECRETS_DIR / DATA_DIR from the env, so tests can point it
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

# Every variable the entrypoint reads. Cleared from the inherited environment by _run
# so each test states its own inputs and nothing leaks in from the shell or CI.
_SCRIPT_INPUTS = frozenset(
    {
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "SECRET_KEY",
        "SECRETS_DIR",
        "DATA_DIR",
    }
)


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    # The ambient environment is inherited so the script has a PATH, but the variables
    # it actually reads are cleared first and then set from `env`. CI exports a
    # DATABASE_URL for the test database, and since the entrypoint now honours a
    # pre-set one, leaving it in place would silently make these tests assert against
    # the runner's environment instead of the script's own logic.
    full_env = {k: v for k, v in os.environ.items() if k not in _SCRIPT_INPUTS}
    full_env.update(env)
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


def test_preset_database_url_wins_over_postgres_vars(tmp_path: Path) -> None:
    # A deployment that authenticates without a password (an Azure managed
    # identity, where the driver fetches a token per connection) has no password
    # to interpolate, so it supplies the whole URL. The POSTGRES_* variables are
    # set here too, to prove they do not override it.
    result = _run(
        {
            **_dirs(tmp_path),
            "DATABASE_URL": "postgresql+asyncpg://id-duckhaven-api@managed.example:5432/duckhaven",
            "POSTGRES_PASSWORD": "pw",
            "POSTGRES_HOST": "ignored",
        },
        "sh",
        "-c",
        "echo $DATABASE_URL",
    )
    assert result.returncode == 0, result.stderr
    assert "postgresql+asyncpg://id-duckhaven-api@managed.example:5432/duckhaven" in result.stdout
    assert "ignored" not in result.stdout


def test_empty_database_url_falls_back_to_postgres_vars(tmp_path: Path) -> None:
    # Set-but-empty is what an unpopulated template variable looks like; it must
    # behave as unset rather than exporting an empty URL the engine cannot parse.
    result = _run(
        {**_dirs(tmp_path), "DATABASE_URL": "", "POSTGRES_PASSWORD": "pw"},
        "sh",
        "-c",
        "echo $DATABASE_URL",
    )
    assert result.returncode == 0, result.stderr
    assert "postgresql+asyncpg://duckhaven:pw@postgres:5432/duckhaven" in result.stdout
