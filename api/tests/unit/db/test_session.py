"""Connection-pool tuning for transparent Postgres failover, and Entra auth."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import create_async_engine

from api.db.entra import attach_entra_auth
from api.db.session import engine_kwargs


def test_postgres_url_enables_pre_ping_and_sizing():
    """The Postgres pool gets pool_pre_ping (so a failed-over primary's stale
    connections are discarded) plus explicit sizing bounds."""
    kwargs = engine_kwargs("postgresql+asyncpg://u:p@host:5432/db")
    assert kwargs["pool_pre_ping"] is True
    assert "pool_size" in kwargs
    assert "max_overflow" in kwargs
    assert "pool_recycle" in kwargs


def test_sqlite_url_passes_no_queue_pool_args():
    """SQLite (unit tests) uses a pool that rejects queue-pool sizing, so we tune
    nothing there."""
    assert engine_kwargs("sqlite+aiosqlite:///:memory:") == {}


@dataclass
class _FakeToken:
    token: str


class _FakeCredential:
    """Stands in for DefaultAzureCredential, counting token requests."""

    def __init__(self, *tokens: str) -> None:
        self._tokens = list(tokens)
        self.calls: list[str] = []

    def get_token(self, scope: str) -> _FakeToken:
        self.calls.append(scope)
        return _FakeToken(self._tokens[min(len(self.calls) - 1, len(self._tokens) - 1)])


def _dispatch_connect(engine, cparams: dict) -> None:
    """Fire the do_connect event the way the dialect would, without a database.

    do_connect is a DialectEvents hook, so it dispatches from the dialect even
    though the listener is registered against the engine.
    """
    dialect = engine.sync_engine.dialect
    dialect.dispatch.do_connect(dialect, None, (), cparams)


def test_entra_auth_supplies_token_as_password():
    engine = create_async_engine("postgresql+asyncpg://id-duckhaven-api@host:5432/db")
    attach_entra_auth(engine, credential=_FakeCredential("token-one"))

    cparams: dict = {}
    _dispatch_connect(engine, cparams)

    assert cparams["password"] == "token-one"


def test_entra_auth_fetches_a_token_per_connection():
    """Tokens expire, so a pooled connection recycled later must not reuse the
    one captured when the engine was built."""
    engine = create_async_engine("postgresql+asyncpg://id-duckhaven-api@host:5432/db")
    credential = _FakeCredential("token-one", "token-two")
    attach_entra_auth(engine, credential=credential)

    first: dict = {}
    second: dict = {}
    _dispatch_connect(engine, first)
    _dispatch_connect(engine, second)

    assert first["password"] == "token-one"
    assert second["password"] == "token-two"
    assert credential.calls == [
        "https://ossrdbms-aad.database.windows.net/.default",
        "https://ossrdbms-aad.database.windows.net/.default",
    ]


def test_no_entra_listener_leaves_password_alone():
    """The default (password) mode must not touch connection parameters."""
    engine = create_async_engine("postgresql+asyncpg://u:p@host:5432/db")

    cparams: dict = {}
    _dispatch_connect(engine, cparams)

    assert cparams == {}
