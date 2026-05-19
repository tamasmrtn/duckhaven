import uuid

import pytest
from agent.results.server import make_results_app
from httpx import ASGITransport, AsyncClient

TOKEN = "test-session-token"


@pytest.fixture
def results_dir(tmp_path):
    return tmp_path


@pytest.fixture
def app(results_dir):
    return make_results_app(results_dir, TOKEN)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_valid_token_existing_file(client, results_dir):
    query_id = uuid.uuid4()
    (results_dir / f"{query_id}.parquet").write_bytes(b"PAR1fake")

    resp = await client.get(
        f"/results/{query_id}.parquet",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.content == b"PAR1fake"


async def test_missing_file_returns_404(client):
    query_id = uuid.uuid4()
    resp = await client.get(
        f"/results/{query_id}.parquet",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 404


async def test_bad_token_returns_401(client, results_dir):
    query_id = uuid.uuid4()
    (results_dir / f"{query_id}.parquet").write_bytes(b"PAR1fake")

    resp = await client.get(
        f"/results/{query_id}.parquet",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


async def test_non_parquet_returns_404(client):
    resp = await client.get(
        "/results/somefile.txt",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 404


async def test_no_auth_header_returns_401(client):
    query_id = uuid.uuid4()
    resp = await client.get(f"/results/{query_id}.parquet")
    assert resp.status_code == 401


async def test_non_uuid_stem_returns_404(client):
    resp = await client.get(
        "/results/not-a-valid-uuid.parquet",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 404
