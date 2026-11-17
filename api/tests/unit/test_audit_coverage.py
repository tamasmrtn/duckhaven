"""Every route that changes state is either audited or exempt, with a reason.

A new POST, PUT, PATCH or DELETE route fails this test until it is placed in
``AUDITED`` (with the actions it records) or ``EXEMPT``. That is the point: an
access change that slips into a new endpoint without an audit event is the gap
the audit log exists to close. The behaviour of each audited route is asserted
by the tests next to it; this one only keeps the inventory honest.
"""

from __future__ import annotations

from api.main import api_app
from api.openapi import _routes
from api.services.audit.registry import ACTIONS

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# (method, path) -> the actions the route records.
AUDITED: dict[tuple[str, str], set[str]] = {
    ("PATCH", "/workspaces/{workspace}/catalogs/{catalog}/access-mode"): {
        "catalog.access_mode_change"
    },
    ("PUT", "/workspaces/{workspace}/catalogs/{catalog}/grants"): {
        "catalog_grant.create",
        "catalog_grant.tier_change",
    },
    ("DELETE", "/workspaces/{workspace}/catalogs/{catalog}/grants/{grant_id}"): {
        "catalog_grant.delete"
    },
    ("POST", "/workspaces/{workspace}/catalogs"): {
        "catalog.create",
        "catalog.attach",
        "catalog_grant.create",
    },
    ("PUT", "/workspaces/{workspace}/catalogs/{catalog}"): {"catalog.attach"},
    ("DELETE", "/workspaces/{workspace}/catalogs/{catalog}"): {"catalog.detach"},
    ("DELETE", "/catalogs/{catalog_id}"): {"catalog.drop", "catalog_grant.delete"},
    ("POST", "/catalogs/{catalog_id}/exports"): {"catalog.export"},
    ("POST", "/catalogs/{catalog_id}/ducklake/rotate-role"): {"catalog.ducklake_role_rotate"},
    ("POST", "/admin/catalogs/ducklake/reconcile-roles"): {"catalog.ducklake_roles_reconcile"},
    ("DELETE", "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}"): {
        "catalog_grant.delete"
    },
    ("DELETE", "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}"): {
        "catalog_grant.delete"
    },
    ("PATCH", "/admin/agents/{agent_id}/access-mode"): {"agent.access_mode_change"},
    ("PUT", "/admin/agents/{agent_id}/grants"): {"agent_grant.create", "agent_grant.tier_change"},
    ("DELETE", "/admin/agents/{agent_id}/grants/{grant_id}"): {"agent_grant.delete"},
}

# Routes not yet wired, each moved into AUDITED by the commit that wires it.
PENDING: set[tuple[str, str]] = {
    ("POST", "/workspaces"),
    ("PATCH", "/workspaces/{workspace}"),
    ("DELETE", "/workspaces/{workspace}"),
    ("POST", "/workspaces/{workspace}/members"),
    ("PUT", "/admin/users/{user_id}/workspaces/{workspace}"),
    ("DELETE", "/admin/users/{user_id}/workspaces/{workspace}"),
    ("POST", "/admin/users"),
    ("PATCH", "/admin/users/{user_id}"),
    ("POST", "/admin/users/{user_id}/revoke-sessions"),
    ("POST", "/admin/service-accounts"),
    ("PATCH", "/admin/service-accounts/{service_account_id}"),
    ("DELETE", "/admin/service-accounts/{service_account_id}"),
    ("POST", "/admin/service-accounts/{service_account_id}/pats"),
    ("DELETE", "/admin/service-accounts/{service_account_id}/pats/{pat_id}"),
    ("POST", "/me/pats"),
    ("DELETE", "/me/pats/{pat_id}"),
    ("POST", "/setup/admin"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
}

# Actions recorded somewhere other than one route's handler.
RECORDED_ELSEWHERE: dict[str, str] = {}

# Actions not yet recorded anywhere; emptied as PENDING is.
PENDING_ACTIONS: set[str] = set(ACTIONS) - set(RECORDED_ELSEWHERE) - set().union(*AUDITED.values())

_WORK = "work inside a workspace, governed by the access the audit log records"
_TABLE = "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}"

# Path prefixes exempt as a whole, with the reason.
EXEMPT_PREFIXES: dict[str, str] = {
    "/workspaces/{workspace}/assistant/": _WORK,
    "/workspaces/{workspace}/dashboard": _WORK,
    "/workspaces/{workspace}/lineage/": _WORK,
    "/workspaces/{workspace}/queries": _WORK,
    "/workspaces/{workspace}/saved-queries": _WORK,
    "/workspaces/{workspace}/schedules": _WORK,
    "/workspaces/{workspace}/semantic/": _WORK,
    "/workspaces/{workspace}/sql/": _WORK,
    "/workspaces/{workspace}/worksheets": _WORK,
    "/sql/sessions/": _WORK,
    "/admin/storage-backends": "storage backends are configuration, not access",
    "/admin/maintenance/": "maintenance is configuration, not access",
    "/maintenance/": "maintenance is configuration, not access",
    "/catalogs/{catalog_id}/migrations": "a storage move keeps the catalog's access as it was",
}

# Single routes exempt, with the reason.
EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/admin/agents/bootstrap"): "agent lifecycle",
    ("POST", "/admin/agents/elastic"): "agent lifecycle",
    ("DELETE", "/admin/agents/{agent_id}"): "agent lifecycle",
    ("DELETE", "/admin/agents/{agent_id}/credential"): "agent lifecycle",
    ("POST", "/admin/agents/{agent_id}/disconnect"): "agent lifecycle",
    ("POST", "/admin/agents/{agent_id}/restart"): "agent lifecycle",
    ("POST", "/admin/agents/{agent_id}/terminate"): "agent lifecycle",
    ("PATCH", "/me"): "the caller's own display preferences",
    ("DELETE", "/queries/{query_id}"): "cancels a running query",
    ("POST", "/catalogs/{catalog_id}/exports/{export_id}/cancel"): (
        "stops an export before its copy exists; the export itself is audited"
    ),
    ("POST", "/workspaces/{workspace}/catalogs/{catalog}/schemas"): "creates an object",
    ("POST", "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables"): (
        "creates an object"
    ),
    ("POST", "/workspaces/{workspace}/catalogs/{catalog}/refresh-stats"): "reads statistics",
    ("POST", f"{_TABLE}/recount"): "reads statistics",
}


def _mutating_routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in _routes(api_app.routes)
        if hasattr(route, "methods")
        for method in (route.methods or set()) & _MUTATING
    }


def _exempt(key: tuple[str, str]) -> bool:
    return key in EXEMPT or any(key[1].startswith(prefix) for prefix in EXEMPT_PREFIXES)


def test_every_mutating_route_is_audited_exempt_or_pending():
    unplaced = sorted(
        key
        for key in _mutating_routes()
        if key not in AUDITED and key not in PENDING and not _exempt(key)
    )
    assert unplaced == [], "Place each route in AUDITED or EXEMPT (with a reason)"


def test_no_route_is_in_two_places():
    for key in AUDITED.keys() | PENDING:
        assert not _exempt(key), f"{key} is both audited and exempt"
    assert not (AUDITED.keys() & PENDING)


def test_every_listed_route_exists():
    routes = _mutating_routes()
    stale = sorted((AUDITED.keys() | PENDING | EXEMPT.keys()) - routes)
    assert stale == []
    for prefix in EXEMPT_PREFIXES:
        assert any(path.startswith(prefix) for _, path in routes), prefix


def test_every_action_is_recorded_somewhere():
    named = set().union(*AUDITED.values()) | set(RECORDED_ELSEWHERE) | PENDING_ACTIONS
    assert named <= set(ACTIONS), sorted(named - set(ACTIONS))
    assert set(ACTIONS) - named == set()
