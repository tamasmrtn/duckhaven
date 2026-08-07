"""One-time bootstrap: create the benchmark's DuckHaven service account,
grant it workspace access, and mint the PAT everything else in this
project runs as (`.env`'s `DUCKHAVEN_PAT`).

DuckHaven has no unattended way to create the *first* service account —
every human sign-in is browser SSO, and `POST /admin/service-accounts`
itself requires an already-authenticated caller (see
`docs/guides/service-accounts.md`). So this still needs a human: someone
with workspace-owner access logs into the DuckHaven UI once, issues
*themselves* a personal access token, and passes it here as `admin_pat` —
used exactly once, for these three calls, and never written anywhere by
this module. What gets persisted afterward is the service account's own
PAT, which is what the benchmark actually authenticates as from then on.
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
    admin_pat: str,
    name: str = "tpch-bench",
    workspace_role: str = "writer",
    pat_expires_in_days: int | None = 90,
) -> ServiceAccountBootstrap:
    """Create the service account, grant it `workspace_role` in `workspace`,
    and issue its PAT — the sequence `docs/guides/service-accounts.md`
    describes as three separate manual UI steps, run here as three API
    calls with `admin_pat`'s authority.

    `workspace_role` defaults to "writer" (not the API's own "reader"
    default): the `write` and `dml` scenarios need it, and the read
    scenarios work fine with the extra grant they don't need.
    """
    api = base_url.rstrip("/") + "/api"
    with httpx.Client(
        base_url=api, headers={"Authorization": f"Bearer {admin_pat}"}, timeout=30.0
    ) as client:
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
