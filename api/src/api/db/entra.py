"""Microsoft Entra token authentication for Azure Database for PostgreSQL.

Lives apart from ``session`` because Alembic builds its own engine and must
attach the same listener; importing ``session`` from ``alembic/env.py`` would
construct the application engine as a side effect of running a migration.
"""

import asyncio
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from api.config import settings


def attach_entra_auth(engine: AsyncEngine, credential: Any = None) -> None:
    """Present a Microsoft Entra access token as the connection password.

    Used when ``db_auth_mode`` is "entra", where the database URL carries no
    password: Azure Database for PostgreSQL accepts an access token in the
    password field, so the credential is minted per connection rather than
    stored anywhere.

    It has to be per connection, not once at startup, because tokens expire — a
    pooled connection recycled after the token's lifetime would otherwise
    reconnect with a dead credential.

    The password is handed to asyncpg as a **coroutine function** rather than a
    string, and asyncpg awaits it while establishing the connection. That matters:
    ``get_token`` is synchronous and does a network round trip whenever its cache
    misses — the first connection after start, and again at every refresh — and the
    ``do_connect`` listener runs on the event-loop thread, so calling it directly
    stalls every request handler, WebSocket heartbeat and reaper tick behind it.
    ``asyncio.to_thread`` keeps that off the loop. Cache hits are a dict lookup and
    cost nothing either way.

    ``credential`` is injectable for tests; production passes nothing and gets
    ``DefaultAzureCredential``, the same ambient-identity chain the storage and
    container-instance clients use.
    """
    if credential is None:
        # Imported here so the password path — compose, tests, any non-Azure
        # deployment — does not pay for azure.identity at import time.
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()

    async def _token() -> str:
        token = await asyncio.to_thread(credential.get_token, settings.db_entra_scope)
        return token.token

    @event.listens_for(engine.sync_engine, "do_connect")
    def _supply_entra_token(dialect, conn_rec, cargs, cparams):  # noqa: ANN001, ANN202
        cparams["password"] = _token
