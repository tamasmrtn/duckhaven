import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.services.compute.backends import get_backend


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def null_backend():
    """The shared NullBackend, cleared before and after each test so its in-process
    instance set never leaks between tests."""
    backend = get_backend("null")
    backend._instances.clear()
    yield backend
    backend._instances.clear()


@pytest.fixture
def elastic_on(monkeypatch, null_backend):
    """Enable elastic compute with the null backend and a per-pool cap of 1."""
    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    monkeypatch.setattr(settings, "elastic_max_agents_per_pool", 1)
    return settings
