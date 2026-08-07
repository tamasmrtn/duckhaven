import httpx
import pytest
import respx

from tpch_bench.azure.setup_service_account import bootstrap_service_account

BASE = "https://fake.example/api"


@respx.mock
def test_bootstrap_creates_grants_and_issues_a_pat():
    respx.post(f"{BASE}/admin/service-accounts").mock(
        return_value=httpx.Response(201, json={"id": "sa-1", "name": "tpch-bench"})
    )
    member_route = respx.post(f"{BASE}/workspaces/ws/members").mock(
        return_value=httpx.Response(
            201, json={"workspace_id": "ws-1", "user_id": "sa-1", "role": "writer"}
        )
    )
    respx.post(f"{BASE}/admin/service-accounts/sa-1/pat").mock(
        return_value=httpx.Response(
            201,
            json={"id": "pat-1", "token": "dh_pat_abc123", "expires_at": "2026-12-01T00:00:00Z"},
        )
    )

    result = bootstrap_service_account(
        base_url="https://fake.example", workspace="ws", admin_pat="dh_pat_admin"
    )

    assert result.service_account_id == "sa-1"
    assert result.pat == "dh_pat_abc123"
    assert result.expires_at == "2026-12-01T00:00:00Z"
    assert member_route.calls[0].request.headers["Authorization"] == "Bearer dh_pat_admin"


@respx.mock
def test_bootstrap_grants_writer_by_default():
    respx.post(f"{BASE}/admin/service-accounts").mock(
        return_value=httpx.Response(201, json={"id": "sa-1", "name": "tpch-bench"})
    )
    member_route = respx.post(f"{BASE}/workspaces/ws/members").mock(
        return_value=httpx.Response(
            201, json={"workspace_id": "ws-1", "user_id": "sa-1", "role": "writer"}
        )
    )
    respx.post(f"{BASE}/admin/service-accounts/sa-1/pat").mock(
        return_value=httpx.Response(201, json={"id": "pat-1", "token": "tok", "expires_at": None})
    )

    bootstrap_service_account(
        base_url="https://fake.example", workspace="ws", admin_pat="dh_pat_admin"
    )

    import json

    assert json.loads(member_route.calls[0].request.content) == {
        "user_id": "sa-1",
        "role": "writer",
    }


@respx.mock
def test_bootstrap_raises_on_a_failed_step_without_issuing_a_pat():
    respx.post(f"{BASE}/admin/service-accounts").mock(
        return_value=httpx.Response(403, json={"detail": "forbidden"})
    )
    pat_route = respx.post(f"{BASE}/admin/service-accounts/sa-1/pat")

    with pytest.raises(httpx.HTTPStatusError):
        bootstrap_service_account(
            base_url="https://fake.example", workspace="ws", admin_pat="dh_pat_admin"
        )

    assert not pat_route.called
