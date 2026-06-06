"""Shared fixtures for api integration tests.

Two real-infrastructure axes, both env-gated so the suite skips cleanly on a
dev box without `make polaris-dev` / a Postgres service:

- **Polaris** — a live Apache Polaris reachable at ``POLARIS_BASE_URL`` (with
  ``POLARIS_CLIENT_ID`` / ``POLARIS_CLIENT_SECRET``). The ``polaris`` /
  ``unique_catalog`` fixtures drive ``PolarisClient`` directly; the live-app
  fixtures (`app_client`, `admin_client`) wire the real client into the running
  FastAPI app.
- **Postgres** — a real database at ``DATABASE_URL``. Each session gets its own
  uniquely-named schema (parallel-safe, no CREATE DATABASE privilege needed);
  each test resets the tables inside it.

Component integration tests run the real ``api_app`` over ASGI transport
(fast, in-process — no agent WebSocket is involved at this layer) against real
Postgres + real Polaris + the bundled MinIO. No FakePolaris, no SQLite.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testkit import polaris as dh_polaris

from api.config import settings
from api.db.base import Base
from api.deps import get_db, get_polaris_client
from api.main import api_app
from api.models.agent import Agent
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.polaris import PolarisClient

# Plain HTTP transport in tests; never emit Secure cookies the test client drops.
settings.cookie_secure = False

ADMIN_EMAIL = "admin@integration.test"
ADMIN_PASSWORD = "integration-pw-123"


@pytest.fixture(scope="session")
def polaris_base_url() -> str:
    """Resolve POLARIS_BASE_URL or skip. Probes the health endpoint to fail fast."""
    url = os.getenv("POLARIS_BASE_URL")
    if not url:
        pytest.skip("POLARIS_BASE_URL not set; skipping Polaris integration test")
    try:
        httpx.get(dh_polaris.health_url(url), timeout=2.0).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Polaris unreachable at {url}: {exc}")
    return url


@pytest.fixture(scope="session", autouse=True)
def _align_s3_settings() -> None:
    """Point the API's S3 settings at the test MinIO so workspace creation
    provisions catalogs Polaris can actually reach. Derived from the same
    POLARIS_S3_* env the Polaris fixtures use; no-op when unset."""
    if bucket := os.getenv("POLARIS_S3_BUCKET"):
        settings.s3_bucket = bucket.split("://", 1)[-1].strip("/")
    if endpoint := os.getenv("POLARIS_S3_ENDPOINT"):
        settings.s3_endpoint = endpoint
    if internal := os.getenv("POLARIS_S3_ENDPOINT_INTERNAL"):
        settings.s3_endpoint_internal = internal


@pytest_asyncio.fixture
async def polaris(polaris_base_url: str) -> AsyncIterator[PolarisClient]:
    """A PolarisClient pointed at the live server, using env credentials."""
    client = PolarisClient(
        base_url=polaris_base_url,
        realm=os.getenv("POLARIS_REALM", "POLARIS"),
        client_id=os.getenv("POLARIS_CLIENT_ID", "root"),
        client_secret=os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t"),
        timeout_s=10.0,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def s3_catalog_storage() -> tuple[str, dict]:
    """(bucket base, *extra* storageConfigInfo) for a bundled-MinIO (S3) catalog.

    Returns only the backend-specific keys (region/endpoint/...) that
    ``PolarisClient.create_catalog`` merges on top of the storageType +
    allowedLocations it derives from the base location."""
    bucket = os.getenv("POLARIS_S3_BUCKET")
    if not bucket:
        pytest.skip("POLARIS_S3_BUCKET not set; skipping Polaris integration test")
    extra: dict = {"region": os.getenv("POLARIS_S3_REGION", "us-east-1")}
    if endpoint := os.getenv("POLARIS_S3_ENDPOINT"):
        extra["endpoint"] = endpoint
        extra["pathStyleAccess"] = True
    if internal := os.getenv("POLARIS_S3_ENDPOINT_INTERNAL"):
        extra["endpointInternal"] = internal
    return bucket.rstrip("/"), extra


@pytest_asyncio.fixture
async def unique_catalog(
    polaris: PolarisClient, s3_catalog_storage: tuple[str, dict]
) -> AsyncIterator[str]:
    """Create a uniquely-named S3 (bundled MinIO) catalog; tear it down on exit."""
    bucket, extra = s3_catalog_storage
    name = f"dh_it_{uuid4().hex[:12]}"
    base = f"{bucket}/{name}"
    await polaris.create_catalog(name, storage_type="S3", base_location=base, extra_storage=extra)
    try:
        yield name
    finally:
        try:
            await polaris.delete_catalog(name)
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


@pytest.fixture
def unique_name() -> Iterator[str]:
    """A short unique identifier safe to use as a catalog object name."""
    yield f"dh_{uuid4().hex[:10]}"


# --- Real Postgres (schema-per-session isolation) ---


@pytest_asyncio.fixture(scope="session")
async def pg_engine():
    """An async engine bound to a throwaway schema in the real database.

    Uses a unique schema (set as the connection ``search_path``) rather than a
    fresh database so no CREATE DATABASE privilege is required and parallel
    workers never collide. Dropped CASCADE on teardown.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; skipping api Postgres integration test")
    schema = f"dh_it_{uuid4().hex[:12]}"
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(pg_engine) -> AsyncIterator[AsyncSession]:
    """Per-test clean schema: create all tables, yield a session, drop all after."""
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(pg_engine, expire_on_commit=False)() as session:
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# --- Live FastAPI app over ASGI transport ---


@pytest_asyncio.fixture
async def app_client(
    db_session: AsyncSession, polaris: PolarisClient
) -> AsyncIterator[AsyncClient]:
    """The real ``api_app`` wired to real Postgres + the real PolarisClient.

    Reuses the unit-test ``dependency_overrides`` pattern but swaps FakePolaris
    for the live client and SQLite for the session-scoped Postgres schema.
    """
    sessionmaker = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    async def override_get_polaris_client() -> PolarisClient:
        return polaris

    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[get_polaris_client] = override_get_polaris_client
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="http://test") as c:
        yield c
    api_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """A persisted admin user (created directly, bypassing the setup-token flow
    which `test_auth_flow` exercises separately)."""
    user = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        name="Integration Admin",
        role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_client(app_client: AsyncClient, admin_user: User) -> AsyncClient:
    """`app_client` with an authenticated admin session cookie."""
    resp = await app_client.post(
        "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return app_client


# --- Polaris catalog cleanup for workspace-creation tests ---


@pytest_asyncio.fixture
async def workspace_factory(admin_client: AsyncClient, polaris: PolarisClient) -> AsyncIterator:
    """Create workspaces via the real API and delete their Polaris catalogs on
    teardown so repeat runs don't accumulate catalogs."""
    created: list[str] = []

    async def _create(slug: str | None = None, name: str = "Integration WS") -> dict:
        slug = slug or f"dh-it-{uuid4().hex[:8]}"
        resp = await admin_client.post("/workspaces", json={"slug": slug, "name": name})
        assert resp.status_code == 201, resp.text
        created.append(slug)
        return resp.json()

    yield _create

    for slug in created:
        try:
            await polaris.delete_catalog(slug)
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


# --- Stub agent in the live registry (Layer-1 dispatch without a real agent) ---


class _StubAgentWS:
    """Captures frames the control plane dispatches; stands in for a real agent
    WebSocket in the process-wide registry. Real agent dispatch is Layer 2."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


@pytest_asyncio.fixture
async def connected_agent(db_session: AsyncSession) -> AsyncIterator[tuple[Agent, _StubAgentWS]]:
    """A healthy agent row registered in the live registry with a stub socket,
    capable of the bundled object_store backend (httpfs)."""
    agent = Agent(
        name="stub-agent",
        status="healthy",
        capabilities={
            "duckdb_version": "1.0.0",
            "extensions": ["httpfs", "iceberg"],
            "memory_limit_gb": 6.0,
            "cores": 4,
        },
        result_host="127.0.0.1",
        result_port=8001,
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    stub = _StubAgentWS()
    registry.register(agent.id, stub)  # type: ignore[arg-type]
    try:
        yield agent, stub
    finally:
        registry.unregister(agent.id)
