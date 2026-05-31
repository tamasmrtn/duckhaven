from httpx import AsyncClient

from api.deps import get_db
from api.main import api_app


async def test_healthz_ok(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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
