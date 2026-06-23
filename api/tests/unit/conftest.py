import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Allow sibling `fake_polaris.py` to be importable from any test under api/tests/unit.
sys.path.insert(0, str(Path(__file__).parent))

from fake_polaris import FakePolaris  # noqa: E402

from api.config import settings  # noqa: E402
from api.db.base import Base  # noqa: E402
from api.deps import get_db, get_polaris_client, get_session_factory  # noqa: E402
from api.main import api_app, app  # noqa: E402
from api.models.catalog import Catalog, WorkspaceCatalog  # noqa: E402
from api.models.storage_backend import StorageBackend  # noqa: E402
from api.models.workspace import Workspace, WorkspaceMember  # noqa: E402


async def seed_workspace(
    db,
    *,
    user_id,
    slug: str = "test-ws",
    name: str = "Test WS",
    role: str | None = "owner",
    backend_kind: str = "object_store",
    catalog_slug: str | None = None,
):
    """Seed a workspace with one default catalog (its own backend) and, when
    ``role`` is set, a membership row. Returns ``(workspace, catalog)``.

    Mirrors the decoupled M:N model: storage lives on the catalog, and the
    workspace reaches tables through a ``WorkspaceCatalog`` binding."""
    backend = StorageBackend(
        kind=backend_kind, name=f"{slug}-store", root_uri="/tmp/test", created_by=user_id
    )
    db.add(backend)
    await db.flush()
    catalog = Catalog(
        slug=catalog_slug or slug.replace("-", "_"),
        name=name,
        polaris_name=slug,
        storage_backend_id=backend.id,
        created_by=user_id,
    )
    db.add(catalog)
    await db.flush()
    ws = Workspace(slug=slug, name=name)
    db.add(ws)
    await db.flush()
    db.add(
        WorkspaceCatalog(
            workspace_id=ws.id, catalog_id=catalog.id, is_default=True, attached_by=user_id
        )
    )
    if role is not None:
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=user_id, role=role))
    await db.commit()
    await db.refresh(ws)
    await db.refresh(catalog)
    return ws, catalog


# Disable secure cookies in tests (plain HTTP transport)
settings.cookie_secure = False

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def fake_polaris() -> FakePolaris:
    """A clean FakePolaris per test; routes via `get_polaris_client` override."""
    return FakePolaris()


@pytest_asyncio.fixture(scope="function")
async def client(db_engine, fake_polaris: FakePolaris):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    async def override_get_polaris_client():
        return fake_polaris

    # The REST routers live on api_app (mounted at /api on the outer app);
    # target it directly so test paths stay unprefixed.
    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[get_polaris_client] = override_get_polaris_client

    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="http://test") as c:
        yield c

    api_app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def ws_client(db_engine):
    """ASGIWebSocketTransport for WebSocket tests.
    Tests must wrap aconnect_ws in their own AsyncClient context so the
    anyio cancel scope inside the transport is entered and exited in the
    same asyncio task (pytest-asyncio tears down fixtures in a different task)."""
    from httpx_ws.transport import ASGIWebSocketTransport

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # The agent WS opens short-lived per-frame sessions from the injected factory,
    # so swap the factory (not a single session) onto the test engine.
    app.dependency_overrides[get_session_factory] = lambda: factory
    yield ASGIWebSocketTransport(app=app)
    app.dependency_overrides.clear()
