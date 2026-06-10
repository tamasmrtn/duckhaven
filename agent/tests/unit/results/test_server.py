import uuid

import duckdb
import pytest
from httpx import ASGITransport, AsyncClient

from agent.auth import TokenHolder
from agent.results.server import make_results_app

TOKEN = "test-session-token"


def _write_result(results_dir, n: int) -> tuple[uuid.UUID, int]:
    """Write a real n-row Parquet result and return its id + byte size."""
    query_id = uuid.uuid4()
    path = results_dir / f"{query_id}.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT i AS n FROM range({n}) t(i)) TO '{path}' (FORMAT PARQUET)"
    )
    return query_id, path.stat().st_size


def _read_ns(tmp_path, content: bytes) -> list[int]:
    """Decode the `n` column from a Parquet response body."""
    out = tmp_path / "decode.parquet"
    out.write_bytes(content)
    rows = duckdb.connect().execute(f"SELECT n FROM read_parquet('{out}')").fetchall()
    return [r[0] for r in rows]


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


async def test_row_window_returns_only_requested_rows(client, results_dir, tmp_path):
    query_id, _ = _write_result(results_dir, 1000)

    resp = await client.get(
        f"/results/{query_id}.parquet?row_offset=100&row_limit=10",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-DH-Row-Offset"] == "100"
    assert _read_ns(tmp_path, resp.content) == list(range(100, 110))


async def test_far_window_does_not_transfer_whole_file(client, results_dir):
    query_id, full_size = _write_result(results_dir, 50_000)

    resp = await client.get(
        f"/results/{query_id}.parquet?row_offset=49000&row_limit=50",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200
    # A 50-row page is far smaller than the full 50k-row result: proof the whole
    # file is never sent across the wire just to read a far page.
    assert len(resp.content) < full_size // 10


async def test_no_window_params_serves_full_file(client, results_dir):
    query_id, full_size = _write_result(results_dir, 100)

    resp = await client.get(
        f"/results/{query_id}.parquet",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200
    assert "X-DH-Row-Offset" not in resp.headers
    assert len(resp.content) == full_size


async def test_invalid_window_param_returns_400(client, results_dir):
    query_id, _ = _write_result(results_dir, 10)

    resp = await client.get(
        f"/results/{query_id}.parquet?row_offset=-5&row_limit=10",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 400
