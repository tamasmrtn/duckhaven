"""Executable form of the API conventions in docs/reference/api-conventions.md.

Every rule in that page that can be checked against the generated schema is
checked here, so an endpoint that drifts fails CI instead of shipping. Prose
conventions are advisory; these are not.

A rule the surface cannot satisfy is a rule to argue with on the page, not one
to weaken here: an assertion edited to match the code it was meant to constrain
stops being a check at all.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import api_app
from api.openapi import _child_routes

METHODS = ("get", "post", "put", "patch", "delete")

# --- Convention data -------------------------------------------------------

#: Path parameters whose name cannot be derived from the preceding collection
#: segment. See "Identifiers" in the conventions page for why each is allowed.
PARAM_NAME_EXCEPTIONS = {
    # /lineage/imports/{provider} and /semantic/imports/{provider}: the producer
    # name *is* the identifier of the import set it asserted.
    "provider",
}

#: Collections that are bounded by deployment topology or by an already-bounded
#: parent, and so are exempt from the pagination envelope. Exhaustive by design:
#: adding to it requires an argument that the collection cannot grow unbounded.
BOUNDED_COLLECTIONS = {
    "/workspaces",
    "/workspaces/{workspace}/members",
    "/catalogs",
    "/workspaces/{workspace}/catalogs",
    "/agents",
    "/admin/agents",
    "/admin/agents/metrics",
    "/admin/storage-backends",
    "/admin/users/{user_id}/workspaces",
    "/admin/service-accounts/{service_account_id}/pats",
    "/workspaces/{workspace}/schedules",
    "/workspaces/{workspace}/semantic/models",
    "/workspaces/{workspace}/semantic/models/{model}/metrics/{metric}/dimensions",
    "/workspaces/{workspace}/catalogs/{catalog}/schemas",
    # A tail, not a page: the client holds a position and polls forward, so it
    # needs a stable `after` even when caught up -- which a cursor that goes null
    # on the last page cannot give it.
    "/catalogs/{catalog_id}/migrations/{migration_id}/logs",
    # Proxied from Iceberg REST, which `services.polaris` calls without a
    # pageToken: the whole identifier list arrives in one response either way, so
    # a cursor here would page a list the server already holds entire. Real
    # paging needs pageToken support in the Polaris client first.
    "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables",
    "/workspaces/{workspace}/catalogs/{catalog}/schemas/{schema}/tables/{table}/snapshots",
}

#: One tag per operation, drawn from this list. Tags become generated-client
#: class names, so a tag spanning unrelated resources produces an unusable class.
ALLOWED_TAGS = {
    "admin-agents",
    "admin-maintenance",
    "admin-service-accounts",
    "admin-storage",
    "admin-users",
    "agents",
    "assistant",
    "auth",
    "catalog",
    "grants",
    "health",
    "lineage",
    "maintenance",
    "metrics",
    "queries",
    "schedules",
    "search",
    "semantic",
    "setup",
    "sql-sessions",
    "workspaces",
}

#: Routes that answer before authentication and so document no 401.
UNAUTHENTICATED = {
    "/healthz",
    "/readyz",
    "/version",
    "/metrics",
    "/setup/status",
    "/setup/admin",
    "/auth/methods",
    "/auth/login",
    "/auth/logout",
    "/auth/oidc/{provider}/login",
    "/auth/oidc/{provider}/callback",
}

#: Abbreviations and kind-names this migration removed. The general rule is
#: enforced by test_path_parameters_are_named_for_their_collection; this is a
#: regression guard, so it names the specific spellings rather than guessing at
#: what looks abbreviated (`pat_id` is short but correct -- the collection is
#: `pats` and the value is a UUID).
BANNED_PARAM_NAMES = frozenset(
    {"ws", "slug", "name", "sa_id", "sq_id", "rec_id", "backend_id", "metric_name"}
)


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> dict:
    return api_app.openapi()


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A client that reaches the real routers. No database is set up, so only
    responses produced before a handler touches one are meaningful here."""
    return TestClient(api_app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def operations(spec) -> list[tuple[str, str, dict]]:
    """Every (path, method, operation) in the REST surface."""
    return [
        (path, method, op)
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in METHODS
    ]


@pytest.fixture(scope="module")
def dependency_names() -> dict[tuple[str, str], set[str]]:
    """Map (path, METHOD) -> the names of every dependency callable it resolves.

    Read off the route's ``dependant`` tree rather than the schema, because the
    schema is exactly what these tests are checking: asking it whether a route
    is authenticated would make the assertion circular.
    """

    def routes(collection):
        """Flatten the router tree to routes with their prefixes applied.

        Shares `_child_routes` with the production code deliberately: if a
        FastAPI upgrade changes how `include_router` nests, both should stop
        walking the tree the same way rather than disagreeing silently.
        """
        for route in collection:
            children = _child_routes(route)
            if children is not None:
                yield from routes(children)
            elif hasattr(route, "dependant") and hasattr(route, "methods"):
                yield route

    def walk(dependant) -> set[str]:
        found = set()
        for sub in dependant.dependencies:
            if sub.call is not None:
                found.add(getattr(sub.call, "__name__", ""))
                # Dependency factories (require_permission, require_agent_tier,
                # target_catalog) return a closure named `_dep`/`_require`, so
                # also record the qualname that identifies the factory.
                found.add(getattr(sub.call, "__qualname__", "").split(".")[0])
            found |= walk(sub)
        return found

    return {
        (route.path, method): walk(route.dependant)
        for route in routes(api_app.routes)
        for method in route.methods
        if method.lower() in METHODS
    }


# --- Helpers ---------------------------------------------------------------


def singular(segment: str) -> str:
    """Singularise a collection segment: `saved-queries` -> `saved_query`."""
    word = segment.replace("-", "_")
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s"):
        return word[:-1]
    return word


def path_params(path: str) -> list[tuple[str, str]]:
    """Yield (preceding_collection_segment, parameter_name) for each `{param}`."""
    segments = path.strip("/").split("/")
    return [
        (segments[i - 1], seg[1:-1])
        for i, seg in enumerate(segments)
        if seg.startswith("{") and i > 0
    ]


def error_codes(op: dict) -> set[str]:
    return {code for code in op.get("responses", {}) if code[0] in "45"}


def failures(items) -> str:
    return "\n".join(f"  {item}" for item in sorted(items))


# --- OpenAPI metadata ------------------------------------------------------


def test_every_operation_sets_an_explicit_operation_id(operations):
    """FastAPI's generated ids embed the path and method, so they churn on every
    rename and collide semantically (`GET /agents` and `GET /admin/agents` are
    both `list_agents`). An explicit id is a stable generated-client method name.
    """
    generated = [
        f"{method.upper()} {path} -> {op.get('operationId')}"
        for path, method, op in operations
        if (op.get("operationId") or "").endswith(f"_{method}")
    ]
    assert not generated, f"auto-generated operationId:\n{failures(generated)}"


def test_operation_ids_are_unique(operations):
    seen: dict[str, str] = {}
    clashes = []
    for path, method, op in operations:
        oid = op.get("operationId")
        where = f"{method.upper()} {path}"
        if oid in seen:
            clashes.append(f"{oid}: {seen[oid]} and {where}")
        seen[oid] = where
    assert not clashes, f"duplicate operationId:\n{failures(clashes)}"


def test_every_operation_has_a_summary_and_description(operations):
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if not op.get("summary") or not op.get("description")
    ]
    assert not missing, f"missing summary or description:\n{failures(missing)}"


def test_every_operation_has_exactly_one_allowed_tag(operations):
    bad = [
        f"{method.upper()} {path} -> {op.get('tags')}"
        for path, method, op in operations
        if len(op.get("tags") or []) != 1 or set(op["tags"]) - ALLOWED_TAGS
    ]
    assert not bad, f"tag is missing, duplicated, or not on the allow-list:\n{failures(bad)}"


def test_authentication_is_described_as_a_security_scheme(spec, operations):
    """Credentials belong in `securitySchemes`, not in every operation's
    parameter list. Left as parameters, a generated client makes the caller pass
    a cookie and a bearer header by hand on every single call.
    """
    schemes = spec.get("components", {}).get("securitySchemes", {})
    assert schemes, "no components.securitySchemes declared"

    leaked = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if {"session", "authorization"}
        & {
            param["name"].lower()
            for param in op.get("parameters", [])
            if param.get("in") in ("cookie", "header")
        }
    ]
    assert not leaked, f"credentials documented as parameters:\n{failures(leaked)}"


# --- Documented error responses -------------------------------------------

# `apply_conventions` derives 401/403/404/409/503 from each route, so asserting
# "every route that should declare X does" only re-runs the derivation and can
# never fail. These check the derivation against the *server* instead: a
# hand-written sample of routes whose behaviour was confirmed by reading the
# handler, plus a live request proving the declared code is the one returned.


#: (method, path, code) triples read off the handlers by hand. Each is a route
#: whose 4xx the schema must document; picked to cover every way authorization
#: happens here -- a dependency guard, a membership check inside the body, and a
#: conflict raised from a service.
DECLARED_BY_HAND = [
    # require_permission dependency
    ("get", "/admin/users", "403"),
    # assert_workspace_member, called inside the handler
    ("get", "/queries/{query_id}", "403"),
    ("delete", "/queries/{query_id}", "403"),
    # _load_session, called inside the handler
    ("get", "/sql/sessions/{session_id}", "403"),
    # _catalog_for_admin raises 403 directly
    ("delete", "/catalogs/{catalog_id}", "403"),
    # conflicts raised in the handler body
    ("post", "/workspaces", "409"),
    ("post", "/admin/users", "409"),
    ("delete", "/admin/storage-backends/{storage_backend_id}", "409"),
    # 503 when a dependency is down
    ("get", "/healthz", "503"),
    ("get", "/readyz", "503"),
    ("post", "/workspaces/{workspace}/sql/sessions", "503"),
    # unauthenticated routes must not claim a 401 they cannot return
    ("get", "/version", "401"),
    ("get", "/healthz", "401"),
]


@pytest.mark.parametrize(("method", "path", "code"), DECLARED_BY_HAND)
def test_known_error_responses_are_declared(spec, method, path, code):
    """Each of these was confirmed by reading the handler it belongs to."""
    operation = spec["paths"][path][method]
    declared = code in operation.get("responses", {})
    unauthenticated = path in UNAUTHENTICATED and code == "401"
    if unauthenticated:
        assert not declared, f"{method.upper()} {path} cannot return {code} but declares it"
    else:
        assert declared, f"{method.upper()} {path} can return {code} but does not declare it"


def test_a_declared_401_is_the_401_the_server_sends(client):
    """The schema says these return 401; prove the server agrees, and that the
    body is the envelope the schema promises rather than something else."""
    resp = client.get("/workspaces")
    assert resp.status_code == 401
    assert resp.json().keys() == {"error", "message", "details"}


def test_an_undeclared_error_still_uses_the_envelope(client):
    """A status no route declares -- 405 from a wrong method -- must still come
    back in the envelope, because the contract covers every 4xx and 5xx, not
    only the ones the schema enumerates."""
    resp = client.request("DELETE", "/version")
    assert resp.status_code == 405
    body = resp.json()
    assert body.keys() == {"error", "message", "details"}
    assert body["error"] == "bad_request", "an unmapped 4xx must not read as a server fault"


# --- Pagination and query parameters ---------------------------------------


def test_limit_is_always_bounded(operations):
    """An unbounded `limit` lets one request ask for the whole table."""
    unbounded = []
    for path, method, op in operations:
        for param in op.get("parameters", []):
            if param["name"] != "limit" or param.get("in") != "query":
                continue
            schema = param.get("schema", {})
            ceiling = schema.get("maximum") or schema.get("exclusiveMaximum")
            if ceiling is None or ceiling > 1000:
                unbounded.append(f"{method.upper()} {path} (maximum={ceiling})")
    assert not unbounded, f"limit is unbounded or above 1000:\n{failures(unbounded)}"


def test_unbounded_collections_return_the_standard_envelope(operations):
    """A collection that grows with usage is paged; a bounded one is exempt.

    Keyed off returning a bare JSON array, which is what makes an endpoint a
    collection for this purpose. Singleton resources (`/me`, `/version`, one
    agent's access policy) return an object and are not collections at all, and
    `RowsPageOut` is a result grid rather than a resource collection.
    """
    offenders = []
    for path, method, op in operations:
        if method != "get" or path in BOUNDED_COLLECTIONS:
            continue
        body = next(
            (
                resp.get("content", {}).get("application/json", {}).get("schema", {})
                for code, resp in op.get("responses", {}).items()
                if code.startswith("2")
            ),
            {},
        )
        if body.get("type") == "array":
            offenders.append(f"GET {path}")
    assert not offenders, f"unbounded collection returning a bare array:\n{failures(offenders)}"


# --- Identifiers -----------------------------------------------------------


def test_no_abbreviated_or_kind_naming_path_parameters(spec):
    banned = [
        f"{path} -> {{{param}}}"
        for path in spec["paths"]
        for _, param in path_params(path)
        if param in BANNED_PARAM_NAMES and param not in PARAM_NAME_EXCEPTIONS
    ]
    assert not banned, f"abbreviated or kind-naming path parameter:\n{failures(banned)}"


def test_path_parameters_are_named_for_their_collection(spec):
    """`{param}` is the singular of the segment before it, `_id` iff it is a UUID."""
    uuid_params = {
        param["name"]
        for item in spec["paths"].values()
        for op in item.values()
        if isinstance(op, dict)
        for param in op.get("parameters", [])
        if param.get("in") == "path" and param.get("schema", {}).get("format") == "uuid"
    }
    bad = []
    for path in spec["paths"]:
        for collection, param in path_params(path):
            if param in PARAM_NAME_EXCEPTIONS or collection.startswith("{"):
                continue
            expected = singular(collection) + ("_id" if param in uuid_params else "")
            if param != expected:
                bad.append(f"{path}: {{{param}}} should be {{{expected}}}")
    assert not bad, f"path parameter does not match its collection:\n{failures(bad)}"


def test_no_route_is_served_at_two_paths(spec):
    """The default-catalog shim duplicates the catalog-scoped family. Two routes
    for one resource is the drift these conventions exist to prevent.
    """
    duplicated = []
    for workspace in ("{workspace}", "{ws}"):  # before and after the phase 4 rename
        legacy = f"/workspaces/{workspace}/schemas"
        canonical = f"/workspaces/{workspace}/catalogs/{{catalog}}/schemas"
        duplicated += [
            path
            for path in spec["paths"]
            if path.startswith(legacy) and canonical + path[len(legacy) :] in spec["paths"]
        ]
    assert not duplicated, f"served at two paths:\n{failures(duplicated)}"


def test_literal_segments_only_neighbour_uuid_identifiers(spec):
    """`/admin/agents/metrics` may sit beside `/admin/agents/{agent_id}` only
    because an agent id is a UUID and no literal can ever collide with one. With
    a slug or a name in that position the literal shadows a real resource.
    """
    uuid_params = {
        param["name"]
        for item in spec["paths"].values()
        for op in item.values()
        if isinstance(op, dict)
        for param in op.get("parameters", [])
        if param.get("in") == "path" and param.get("schema", {}).get("format") == "uuid"
    }
    # prefix -> the identifier parameter that occupies that slot
    id_slots: dict[str, str] = {}
    for path in spec["paths"]:
        segments = path.strip("/").split("/")
        for i, seg in enumerate(segments):
            if seg.startswith("{"):
                id_slots["/".join(segments[:i])] = seg[1:-1]

    hazards = []
    for path in spec["paths"]:
        segments = path.strip("/").split("/")
        for i, seg in enumerate(segments):
            if seg.startswith("{"):
                continue
            sibling = id_slots.get("/".join(segments[:i]))
            if sibling and sibling not in uuid_params:
                hazards.append(f"/{'/'.join(segments[: i + 1])} shadows {{{sibling}}}")
    assert not hazards, f"literal segment beside a non-UUID identifier:\n{failures(hazards)}"


# --- Errors and status codes -----------------------------------------------


def test_every_error_response_uses_the_error_envelope(spec, operations):
    offenders = []
    for path, method, op in operations:
        for code, resp in op.get("responses", {}).items():
            if code[0] not in "45":
                continue
            body = resp.get("content", {}).get("application/json", {}).get("schema", {})
            if body.get("$ref", "").rsplit("/", 1)[-1] != "ErrorOut":
                offenders.append(f"{method.upper()} {path} {code}")
    assert not offenders, f"error response is not ErrorOut:\n{failures(offenders)}"


def test_creation_responses_let_the_caller_reach_what_was_made(spec, operations):
    """A 201 either returns the created resource or says where it is.

    RFC 9110 makes `Location` a SHOULD, not a MUST, and every creating endpoint
    here returns the resource itself -- which is strictly more useful than a URL
    to fetch it from. So the rule is the underlying requirement rather than the
    header: the caller must come away able to reach what was created. `Location`
    is required only where the resource is *not* returned, and is added anyway
    where its address cannot be derived from the request path (opening a SQL
    session, which lands under `/sql/sessions/{session_id}`).
    """
    unreachable = []
    for path, method, op in operations:
        created = op.get("responses", {}).get("201")
        if created is None:
            continue
        has_body = bool(created.get("content", {}).get("application/json", {}).get("schema"))
        has_location = "location" in {h.lower() for h in created.get("headers", {})}
        if not has_body and not has_location:
            unreachable.append(f"{method.upper()} {path}")
    assert not unreachable, (
        f"201 returns neither the resource nor a Location:\n{failures(unreachable)}"
    )


def test_every_pagination_exemption_is_documented():
    """The exemption list above and the one in the conventions page must agree.

    Both are normative and they are edited in different places, so without this
    a route quietly added to the code list never reaches the page a consumer
    reads -- and an exemption is exactly the kind of decision that has to be
    written down to stay a decision rather than an accident.
    """
    page = Path(__file__).parents[3] / "docs" / "reference" / "api-conventions.md"
    cited = set(re.findall(r"`(/[a-z0-9{}_/-]+)`", page.read_text()))
    undocumented = sorted(BOUNDED_COLLECTIONS - cited)
    assert not undocumented, (
        "exempt from pagination but not named in docs/reference/api-conventions.md:\n"
        f"{failures(undocumented)}"
    )
