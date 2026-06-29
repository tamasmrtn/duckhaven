from httpx import AsyncClient

from api.deps import get_db, get_polaris_client
from api.main import api_app


async def test_healthz_ok(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_ok(client: AsyncClient):
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_readyz_503_when_draining(client: AsyncClient):
    api_app.state.draining = True
    try:
        resp = await client.get("/readyz")
    finally:
        api_app.state.draining = False
    assert resp.status_code == 503
    assert resp.json()["detail"] == "draining"


async def test_readyz_503_when_polaris_unreachable(client: AsyncClient):
    class _BrokenPolaris:
        async def ping(self):
            raise RuntimeError("connection refused")

    api_app.dependency_overrides[get_polaris_client] = lambda: _BrokenPolaris()
    try:
        resp = await client.get("/readyz")
    finally:
        del api_app.dependency_overrides[get_polaris_client]
    assert resp.status_code == 503
    assert "polaris unreachable" in resp.json()["detail"]


async def test_healthz_503_when_db_unreachable(client: AsyncClient):
    async def broken_db():
        class _S:
            async def execute(self, _):
                raise RuntimeError("connection refused")

        yield _S()

    api_app.dependency_overrides[get_db] = broken_db
    try:
        resp = await client.get("/healthz")
    finally:
        # client fixture's teardown also clears, but be explicit about scope.
        del api_app.dependency_overrides[get_db]
    assert resp.status_code == 503
    assert "database unreachable" in resp.json()["detail"]
