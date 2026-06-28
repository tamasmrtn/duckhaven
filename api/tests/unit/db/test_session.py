"""Connection-pool tuning for transparent Postgres failover."""

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
