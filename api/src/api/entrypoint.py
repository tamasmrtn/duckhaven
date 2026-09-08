"""Container entrypoint: generates first-boot secrets, applies migrations, then
execs the CMD. Python port of deploy/api-entrypoint.sh — the distroless runtime
image has no shell to run that script in. This folds in the former init-secrets
one-shot: there is no separate bootstrap container, so the api service prepares
its own secrets idempotently on every start.

Invoked as ``ENTRYPOINT ["python", "-m", "api.entrypoint"]``; sys.argv[1:] is the
CMD Docker appends (e.g. ``uvicorn api.main:app ...``).
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path


def _generate_secret() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def _is_populated(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _write_if_absent(path: Path, env_val: str) -> None:
    """Write a file only if it does not already exist and is non-empty.

    An env-provided value is captured on first boot and persisted; on later
    boots the file wins so the stack survives a missing .env.
    """
    if _is_populated(path):
        return
    path.write_text(env_val if env_val else _generate_secret())
    path.chmod(0o644)


def _database_url() -> str:
    # A DATABASE_URL supplied by the environment wins unconditionally: assembling
    # one here can only ever express user-and-password auth, and a passwordless
    # connection (e.g. Azure managed identity, where the driver fetches a
    # short-lived token per connection) has no password to put in a URL at all.
    database_url = os.environ.get("DATABASE_URL") or ""
    if database_url:
        return database_url
    password = os.environ.get("POSTGRES_PASSWORD") or "duckhaven"
    user = os.environ.get("POSTGRES_USER") or "duckhaven"
    host = os.environ.get("POSTGRES_HOST") or "postgres"
    port = os.environ.get("POSTGRES_PORT") or "5432"
    name = os.environ.get("POSTGRES_DB") or "duckhaven"
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


def _apply_migrations(alembic_ini: Path) -> None:
    # Only runs inside the built image where /app/alembic.ini exists; in unit
    # tests of this module the file is absent and the step is skipped.
    if alembic_ini.is_file():
        subprocess.run(["alembic", "-c", str(alembic_ini), "upgrade", "head"], check=True)


def main(argv: list[str]) -> None:
    secrets_dir = Path(os.environ.get("SECRETS_DIR") or "/var/duckhaven/secrets")
    data_dir = Path(os.environ.get("DATA_DIR") or "/var/duckhaven")
    secrets_dir.mkdir(parents=True, exist_ok=True)

    secret_key_path = secrets_dir / "secret_key"
    # The setup_token gates the browser-driven first-admin creation (POST
    # /api/setup/admin); it is generated ONLY on first boot so a stranger
    # reading the volume after the operator has already created the admin
    # cannot mint a fresh token.
    first_boot = not _is_populated(secret_key_path)

    _write_if_absent(secret_key_path, os.environ.get("SECRET_KEY") or "")

    if first_boot:
        token_path = data_dir / "setup_token"
        if not _is_populated(token_path):
            token_path.write_text(_generate_secret())
            token_path.chmod(0o644)

    os.environ["SECRET_KEY"] = secret_key_path.read_text()
    os.environ["DATABASE_URL"] = _database_url()

    _apply_migrations(Path("/app/alembic.ini"))

    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main(sys.argv[1:])
