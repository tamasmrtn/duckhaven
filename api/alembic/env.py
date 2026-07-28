import asyncio
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import api.models  # noqa: F401 — registers all models with Base
from api.config import settings
from api.db.base import Base
from api.db.entra import attach_entra_auth

# Transaction-level advisory lock that serializes concurrent `alembic upgrade`
# runs across API replicas (every replica migrates on boot). The first runner
# holds it for the migration transaction; others block here and then find the DB
# already at head. Auto-released on commit. Arbitrary unique key ('dhmg').
_MIGRATION_LOCK_KEY = 0x64686D67

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Serialize concurrent replicas: only one runs migrations at a time.
        if connection.dialect.name == "postgresql":
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": _MIGRATION_LOCK_KEY}
            )
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # Alembic builds its own engine rather than reusing api.db.session's, so the
    # Entra token listener has to be attached here too -- otherwise migrations are
    # the one thing that still needs a password, and they run before the app does.
    if settings.db_auth_mode == "entra":
        attach_entra_auth(connectable)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
