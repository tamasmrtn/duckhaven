import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from agent.auth import TokenHolder
from agent.results.server import make_results_app

TOKEN = "test-session-token"


@pytest.fixture
def results_dir(tmp_path):
    return tmp_path


@pytest.fixture
def app(results_dir):
    return make_results_app(results_dir, TokenHolder(TOKEN))


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


async def test_token_set_after_construction(results_dir):
    """The server reads the holder per request, so a token set after the app is
    built (as happens once the control channel authenticates) is honored, and
    an empty holder fails closed before auth completes."""
    holder = TokenHolder()
    app = make_results_app(results_dir, holder)
    query_id = uuid.uuid4()
    (results_dir / f"{query_id}.parquet").write_bytes(b"PAR1fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Pre-auth: holder empty -> any bearer (incl. empty) is rejected.
        pre = await c.get(
            f"/results/{query_id}.parquet",
            headers={"Authorization": "Bearer "},
        )
        assert pre.status_code == 401

        holder.value = "late-token"
        resp = await c.get(
            f"/results/{query_id}.parquet",
            headers={"Authorization": "Bearer late-token"},
        )
        assert resp.status_code == 200
        assert resp.content == b"PAR1fake"
