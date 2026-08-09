import json

import httpx
import pytest
import respx

from tpch_bench.azure.setup_service_account import bootstrap_service_account

BASE = "https://fake.example/api"


def _mock_login():
    return respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"id": "admin-1", "email": "admin@admin.com"})
    )


@respx.mock
def test_bootstrap_logs_in_creates_grants_and_issues_a_pat():
    login_route = _mock_login()
    respx.post(f"{BASE}/admin/service-accounts").mock(
        return_value=httpx.Response(201, json={"id": "sa-1", "name": "tpch-bench"})
    )
    respx.post(f"{BASE}/workspaces/ws/members").mock(
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
        base_url="https://fake.example",
        workspace="ws",
        admin_email="admin@admin.com",
        admin_password="TestPassword123",
    )

    assert result.service_account_id == "sa-1"
    assert result.pat == "dh_pat_abc123"
    assert result.expires_at == "2026-12-01T00:00:00Z"
    assert json.loads(login_route.calls[0].request.content) == {
        "email": "admin@admin.com",
        "password": "TestPassword123",
    }


@respx.mock
def test_bootstrap_grants_writer_by_default():
    _mock_login()
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
        base_url="https://fake.example",
        workspace="ws",
        admin_email="admin@admin.com",
        admin_password="TestPassword123",
    )

    assert json.loads(member_route.calls[0].request.content) == {
        "user_id": "sa-1",
        "role": "writer",
    }


@respx.mock
def test_bootstrap_raises_on_a_failed_login_without_creating_anything():
    _mock_login_failure = respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(401, json={"detail": "invalid credentials"})
    )
    create_route = respx.post(f"{BASE}/admin/service-accounts")

    with pytest.raises(httpx.HTTPStatusError):
        bootstrap_service_account(
            base_url="https://fake.example",
            workspace="ws",
            admin_email="admin@admin.com",
            admin_password="wrong",
        )

    assert not create_route.called


@respx.mock
def test_bootstrap_raises_on_a_failed_step_without_issuing_a_pat():
    _mock_login()
    respx.post(f"{BASE}/admin/service-accounts").mock(
        return_value=httpx.Response(403, json={"detail": "forbidden"})
    )
    pat_route = respx.post(f"{BASE}/admin/service-accounts/sa-1/pat")

    with pytest.raises(httpx.HTTPStatusError):
        bootstrap_service_account(
            base_url="https://fake.example",
            workspace="ws",
            admin_email="admin@admin.com",
            admin_password="TestPassword123",
        )

    assert not pat_route.called
