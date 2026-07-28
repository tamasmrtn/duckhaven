"""Create Microsoft Entra login roles on Azure Database for PostgreSQL.

Run once per environment, from inside the network that can reach the server —
in the Azure deployment that is a manual-trigger Container Apps job running this
same API image, because the server has no public endpoint and a Terraform runner
outside the VNet cannot reach it.

Why it exists at all: an Entra identity can authenticate to the server, but it
cannot *log in* until a database role exists for it. The alternative is to
register the application's own identity as the server's Entra administrator,
which works with no bootstrap step but hands the internet-facing app
``azure_pg_admin`` over every database on the server. Instead a separate
bootstrap identity holds that admin role, and it is used exactly once, here, to
create ordinary login roles for the workload identities.

``pgaadauth_create_principal`` is only defined in the ``postgres`` database, so
principal creation and the per-database grants are two separate connections.

Environment:
    DB_BOOTSTRAP_HOST         server FQDN
    DB_BOOTSTRAP_USER         the bootstrap identity's name (an Entra admin)
    DB_BOOTSTRAP_PRINCIPALS   comma-separated identity names to create roles for
    DB_BOOTSTRAP_DATABASES    comma-separated databases to grant those roles on
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import asyncpg

from api.config import settings

# Entra identity and database names are Azure resource names. Anything outside
# this set would need escaping we deliberately do not implement: these values
# come from the deployment's own configuration, and a name that does not match
# is a misconfiguration worth failing on rather than quoting around.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _quote(name: str) -> str:
    """Quote an identifier, rejecting anything that is not a plain Azure name."""
    if not _SAFE_NAME.match(name):
        raise ValueError(f"refusing to use {name!r} as an identifier")
    return f'"{name}"'


def _names(env_var: str) -> list[str]:
    return [part.strip() for part in os.environ.get(env_var, "").split(",") if part.strip()]


async def _connect(host: str, user: str, database: str, token: str) -> asyncpg.Connection:
    # sslmode=require, not the driver default: Azure enforces TLS, and stating it
    # means a misconfiguration fails loudly instead of silently downgrading.
    return await asyncpg.connect(
        host=host, port=5432, user=user, password=token, database=database, ssl="require"
    )


async def _create_principals(conn: asyncpg.Connection, principals: list[str]) -> None:
    for principal in principals:
        # Idempotent by inspection rather than by exception: the function raises a
        # plain error when the role exists, which is indistinguishable from a real
        # failure without parsing its message.
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", principal)
        if exists:
            print(f"role {principal} already exists")
            continue
        await conn.fetchval("SELECT pgaadauth_create_principal($1, false, false)", principal)
        print(f"created role {principal}")


async def _grant(conn: asyncpg.Connection, database: str, principals: list[str]) -> None:
    for principal in principals:
        role = _quote(principal)
        await conn.execute(f"GRANT CONNECT ON DATABASE {_quote(database)} TO {role}")
        # CREATE as well as USAGE: the API applies its own Alembic migrations on
        # start, so it has to be able to create the schema it then owns.
        await conn.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {role}")
        await conn.execute(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {role}")
        await conn.execute(f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {role}")
        print(f"granted {principal} on {database}")


async def main() -> int:
    host = os.environ.get("DB_BOOTSTRAP_HOST", "")
    user = os.environ.get("DB_BOOTSTRAP_USER", "")
    principals = _names("DB_BOOTSTRAP_PRINCIPALS")
    databases = _names("DB_BOOTSTRAP_DATABASES")

    if not host or not user or not principals or not databases:
        print(
            "DB_BOOTSTRAP_HOST, DB_BOOTSTRAP_USER, DB_BOOTSTRAP_PRINCIPALS and "
            "DB_BOOTSTRAP_DATABASES are all required",
            file=sys.stderr,
        )
        return 2

    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token(settings.db_entra_scope).token

    conn = await _connect(host, user, "postgres", token)
    try:
        await _create_principals(conn, principals)
    finally:
        await conn.close()

    for database in databases:
        conn = await _connect(host, user, database, token)
        try:
            await _grant(conn, database, principals)
        finally:
            await conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
