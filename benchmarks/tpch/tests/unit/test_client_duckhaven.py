import httpx
import pytest
import respx

from tpch_bench.clients.duckhaven import DuckHavenClient

HOST = "https://fake.example"
BASE = f"{HOST}/api"


def _mock_session_open(agent_id: str | None = "agent-1") -> None:
    body = {"id": "sess-1", "status": "open"}
    if agent_id is not None:
        body["agent_id"] = agent_id
    respx.post(f"{BASE}/workspaces/ws/sql/sessions").mock(
        return_value=httpx.Response(200, json=body)
    )


@respx.mock
def test_connect_opens_a_session_and_is_idempotent():
    _mock_session_open()

    client = DuckHavenClient(host=HOST, workspace="ws", pat="tok")
    client.connect()

    assert client._conn is not None
    assert client._conn.agent_id == "agent-1"

    client.connect()

    assert respx.calls.call_count == 1


@respx.mock
def test_run_statement_returns_rich_metadata_from_supplementary_calls():
    _mock_session_open()
    respx.post(f"{BASE}/sql/sessions/sess-1/statements").mock(
        return_value=httpx.Response(202, json={"id": "q-1", "status": "queued"})
    )
    query_detail = {
        "id": "q-1",
        "status": "done",
        "duration_ms": 120.5,
        "started_at": "2026-01-01T00:00:00.000Z",
        "running_at": "2026-01-01T00:00:00.010Z",
        "finished_at": "2026-01-01T00:00:00.130Z",
        "row_count": 3,
    }
    respx.get(f"{BASE}/queries/q-1").mock(return_value=httpx.Response(200, json=query_detail))
    respx.get(f"{BASE}/queries/q-1/rows").mock(
        return_value=httpx.Response(
            200,
            json={"columns": ["n"], "rows": [{"n": 1}, {"n": 2}, {"n": 3}], "cursor": None},
        )
    )
    respx.get(f"{BASE}/queries/q-1/profile").mock(
        return_value=httpx.Response(
            200,
            json={
                "summary": {"peak_memory_bytes": 4096, "spill_bytes": 0},
                "tree": {},
            },
        )
    )

    client = DuckHavenClient(host=HOST, workspace="ws", pat="tok")
    result = client.run_statement("SELECT 1", timeout_s=5.0)

    assert result.engine_query_id == "q-1"
    assert result.server_duration_ms == 120.5
    assert result.queued_ms == pytest.approx(10.0)
    assert result.execution_ms == pytest.approx(120.0)
    assert result.row_count == 3
    assert result.peak_memory_bytes == 4096
    assert result.spill_bytes == 0
    assert result.compute_ref == "agent-1"
    assert result.error is None


@respx.mock
def test_run_statement_maps_a_failed_query_to_a_result_error_without_raising():
    _mock_session_open()
    respx.post(f"{BASE}/sql/sessions/sess-1/statements").mock(
        return_value=httpx.Response(202, json={"id": "q-2", "status": "queued"})
    )
    respx.get(f"{BASE}/queries/q-2").mock(
        return_value=httpx.Response(200, json={"id": "q-2", "status": "failed", "error": "boom"})
    )

    client = DuckHavenClient(host=HOST, workspace="ws", pat="tok")
    result = client.run_statement("SELECT bad", timeout_s=5.0)

    assert result.error is not None
    assert "boom" in result.error
    assert result.row_count is None


@respx.mock
def test_run_statement_tolerates_a_server_without_the_profile_endpoint():
    _mock_session_open()
    respx.post(f"{BASE}/sql/sessions/sess-1/statements").mock(
        return_value=httpx.Response(202, json={"id": "q-3", "status": "queued"})
    )
    respx.get(f"{BASE}/queries/q-3").mock(
        return_value=httpx.Response(200, json={"id": "q-3", "status": "done", "row_count": 0})
    )
    respx.get(f"{BASE}/queries/q-3/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "cursor": None})
    )
    respx.get(f"{BASE}/queries/q-3/profile").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    client = DuckHavenClient(host=HOST, workspace="ws", pat="tok")
    result = client.run_statement("CREATE TABLE t (a INT)", timeout_s=5.0)

    assert result.error is None
    assert result.peak_memory_bytes is None
    assert result.spill_bytes is None


@respx.mock
def test_close_deletes_the_session_and_is_safe_when_not_connected():
    client = DuckHavenClient(host=HOST, workspace="ws", pat="tok")
    client.close()  # never connected: no-op, must not raise

    _mock_session_open()
    respx.delete(f"{BASE}/sql/sessions/sess-1").mock(return_value=httpx.Response(204))

    client.connect()
    client.close()

    assert client._conn is None
