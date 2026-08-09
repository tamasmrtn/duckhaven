"""One-time bootstrap: create the benchmark's DuckHaven service account,
grant it workspace access, and mint the PAT everything else in this
project runs as (`.env`'s `DUCKHAVEN_PAT`).

DuckHaven has no unattended way to create the *first* service account, and
— verified directly against a running deployment while building this —
**no PAT-issuance feature for human users at all**: PATs are a
service-account-only concept (`docs/guides/service-accounts.md`). A human
admin's only credential for `POST /admin/service-accounts` is therefore
whatever they sign in with — a session cookie, from `POST /auth/login` —
not a bearer token. So this logs in as a local (password-auth) admin once,
runs the three bootstrap calls on that session, and never persists the
password or the session past this function returning. What gets persisted
afterward is the service account's own PAT, which is what the benchmark
actually authenticates as from then on.

This only covers local/password admins. An SSO-only deployment (DuckHaven's
Azure Entra ID login, used from Phase 1 onward per plan §6) has no password
to log in with here either — bootstrapping the service account there still
needs the documented manual browser flow, with its resulting PAT handed to
this project directly rather than through this function.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ServiceAccountBootstrap:
    service_account_id: str
    pat: str
    expires_at: str | None


def bootstrap_service_account(
    *,
    base_url: str,
    workspace: str,
    admin_email: str,
    admin_password: str,
    name: str = "tpch-bench",
    workspace_role: str = "writer",
    pat_expires_in_days: int | None = 90,
) -> ServiceAccountBootstrap:
    """Log in as `admin_email` (a local, password-auth admin), create the
    service account, grant it `workspace_role` in `workspace`, and issue
    its PAT — the sequence `docs/guides/service-accounts.md` describes as
    manual UI steps, run here as one login plus three API calls on the
    resulting session.

    `workspace_role` defaults to "writer" (not the API's own "reader"
    default): the `write` and `dml` scenarios need it, and the read
    scenarios work fine with the extra grant they don't need.
    """
    api = base_url.rstrip("/") + "/api"
    with httpx.Client(base_url=api, timeout=30.0) as client:
        login_resp = client.post(
            "/auth/login", json={"email": admin_email, "password": admin_password}
        )
        login_resp.raise_for_status()

        create_resp = client.post("/admin/service-accounts", json={"name": name, "role": "user"})
        create_resp.raise_for_status()
        service_account_id = create_resp.json()["id"]

        member_resp = client.post(
            f"/workspaces/{workspace}/members",
            json={"user_id": service_account_id, "role": workspace_role},
        )
        member_resp.raise_for_status()

        pat_resp = client.post(
            f"/admin/service-accounts/{service_account_id}/pat",
            json={"expires_in_days": pat_expires_in_days},
        )
        pat_resp.raise_for_status()
        pat_body = pat_resp.json()

    return ServiceAccountBootstrap(
        service_account_id=service_account_id,
        pat=pat_body["token"],
        expires_at=pat_body.get("expires_at"),
    )
