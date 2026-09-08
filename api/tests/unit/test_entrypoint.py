"""Tests for the api container entrypoint (api.entrypoint).

Python port of deploy/api-entrypoint.sh — see api/src/api/entrypoint.py. Most
tests call main() in-process with os.execvp monkeypatched to a recorder instead
of a real process replacement; one subprocess-based test exercises the real
execvp handoff end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from api import entrypoint

# Every env var the entrypoint reads. Cleared before each test so each test
# states its own inputs and nothing leaks in from the ambient environment.
_SCRIPT_INPUTS = (
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "SECRET_KEY",
    "SECRETS_DIR",
    "DATA_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SCRIPT_INPUTS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def exec_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Prevent main() from replacing the test process; record the handoff instead."""
    calls: list[list[str]] = []

    def fake_execvp(file: str, args: list[str]) -> None:
        calls.append(list(args))

    monkeypatch.setattr(entrypoint.os, "execvp", fake_execvp)
    return calls


def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_generates_secret_key_and_setup_token_on_first_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    entrypoint.main(["true"])

    secret_key = tmp_path / "secrets" / "secret_key"
    setup_token = tmp_path / "setup_token"
    assert secret_key.is_file() and setup_token.is_file()
    # Non-empty, no trailing newline; random base64 of 32 bytes is ~44 chars.
    assert secret_key.read_text() and "\n" not in secret_key.read_text()
    assert len(secret_key.read_text()) >= 40
    assert setup_token.read_text()
    assert exec_calls == [["true"]]


def test_secret_key_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    entrypoint.main(["true"])
    first = (tmp_path / "secrets" / "secret_key").read_text()
    entrypoint.main(["true"])
    second = (tmp_path / "secrets" / "secret_key").read_text()
    assert first == second, "second run must not regenerate the existing secret"


def test_honours_secret_key_override_on_first_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "operator-supplied-key")
    entrypoint.main(["true"])
    assert (tmp_path / "secrets" / "secret_key").read_text() == "operator-supplied-key"


def test_secret_key_override_ignored_after_first_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "first")
    entrypoint.main(["true"])
    monkeypatch.setenv("SECRET_KEY", "second")
    entrypoint.main(["true"])
    assert (tmp_path / "secrets" / "secret_key").read_text() == "first"


def test_does_not_regenerate_setup_token_after_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    # First boot: token generated.
    entrypoint.main(["true"])
    # API consumed the token after first-admin creation.
    (tmp_path / "setup_token").unlink()
    # Subsequent boot must NOT mint a new one — otherwise a stranger reading
    # the volume could create a second admin.
    entrypoint.main(["true"])
    assert not (tmp_path / "setup_token").exists()


def test_exports_secret_key_and_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pg-test-pw")
    entrypoint.main(["true"])
    assert os.environ["SECRET_KEY"] == "test-secret"
    assert (
        os.environ["DATABASE_URL"]
        == "postgresql+asyncpg://duckhaven:pg-test-pw@postgres:5432/duckhaven"
    )


def test_database_url_defaults_password_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    # Empty POSTGRES_PASSWORD falls back to the shared default.
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    entrypoint.main(["true"])
    assert (
        os.environ["DATABASE_URL"]
        == "postgresql+asyncpg://duckhaven:duckhaven@postgres:5432/duckhaven"
    )


def test_respects_postgres_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_USER", "alt_user")
    monkeypatch.setenv("POSTGRES_HOST", "altdb")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DB", "alt_db")
    entrypoint.main(["true"])
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://alt_user:pw@altdb:6543/alt_db"


def test_preset_database_url_wins_over_postgres_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    # A deployment that authenticates without a password (an Azure managed
    # identity, where the driver fetches a token per connection) has no
    # password to interpolate, so it supplies the whole URL. POSTGRES_* is set
    # too, to prove it does not override it.
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://id-duckhaven-api@managed.example:5432/duckhaven"
    )
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_HOST", "ignored")
    entrypoint.main(["true"])
    assert (
        os.environ["DATABASE_URL"]
        == "postgresql+asyncpg://id-duckhaven-api@managed.example:5432/duckhaven"
    )


def test_empty_database_url_falls_back_to_postgres_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exec_calls: list[list[str]]
) -> None:
    # Set-but-empty is what an unpopulated template variable looks like; it
    # must behave as unset rather than exporting an empty URL the engine
    # cannot parse.
    _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    entrypoint.main(["true"])
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://duckhaven:pw@postgres:5432/duckhaven"


def test_aborts_when_migrations_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    alembic_ini = tmp_path / "alembic.ini"
    alembic_ini.write_text("")

    def fake_run(cmd: list[str], check: bool) -> None:
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd)

    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        entrypoint._apply_migrations(alembic_ini)


def test_execs_the_cmd_replacing_the_process(tmp_path: Path) -> None:
    # Exercises the real os.execvp handoff (not monkeypatched) in a subprocess,
    # so the exit code observed here is the exec'd command's own exit code —
    # proving the entrypoint process was replaced in place (matching the shell
    # version's `exec "$@"`), not spawned as a child of a still-running parent.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.entrypoint",
            sys.executable,
            "-c",
            "import sys; sys.exit(42)",
        ],
        env={
            **os.environ,
            "SECRETS_DIR": str(tmp_path / "secrets"),
            "DATA_DIR": str(tmp_path),
        },
        check=False,
    )
    assert result.returncode == 42
