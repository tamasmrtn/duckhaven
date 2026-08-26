"""Executable form of the API conventions in docs/reference/api-conventions.md.

Every rule in that page that can be checked against the generated schema is
checked here, so an endpoint that drifts fails CI instead of shipping. Prose
conventions are advisory; these are not.

Rules that the surface does not satisfy yet are marked ``xfail(strict=True)``
and carry the phase of docs/developer/api-consistency-plan.md that clears them.
``strict=True`` matters: a rule that starts passing early also fails, so a phase
cannot quietly land work belonging to a later one. Clearing a phase means
deleting its marker, never editing the assertion to match the code.
"""

import re

import pytest
from fastapi.routing import _IncludedRouter

from api.main import api_app

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

#: Abbreviated or kind-naming parameters the conventions forbid outright.
BANNED_PARAM_NAMES = re.compile(r"^(ws|slug|name|[a-z]{2,3}_id)$")


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> dict:
    return api_app.openapi()


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

        ``include_router`` wraps each child in an ``_IncludedRouter`` whose
        ``effective_candidates()`` carry the combined path; the raw
        ``original_router.routes`` still hold the unprefixed paths.
        """
        for route in collection:
            if isinstance(route, _IncludedRouter):
                yield from routes(route.effective_candidates())
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


# --- OpenAPI metadata (plan phase 3) ---------------------------------------


@pytest.mark.xfail(strict=True, reason="plan phase 3: explicit operation ids")
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


@pytest.mark.xfail(strict=True, reason="plan phase 3: summaries and descriptions")
def test_every_operation_has_a_summary_and_description(operations):
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if not op.get("summary") or not op.get("description")
    ]
    assert not missing, f"missing summary or description:\n{failures(missing)}"


@pytest.mark.xfail(strict=True, reason="plan phase 3: split the admin tag")
def test_every_operation_has_exactly_one_allowed_tag(operations):
    bad = [
        f"{method.upper()} {path} -> {op.get('tags')}"
        for path, method, op in operations
        if len(op.get("tags") or []) != 1 or set(op["tags"]) - ALLOWED_TAGS
    ]
    assert not bad, f"tag is missing, duplicated, or not on the allow-list:\n{failures(bad)}"


@pytest.mark.xfail(strict=True, reason="plan phase 3: security schemes")
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


# --- Documented error responses (plan phase 3) -----------------------------


@pytest.mark.xfail(strict=True, reason="plan phase 3: documented 401")
def test_authenticated_operations_document_401(operations, dependency_names):
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if path not in UNAUTHENTICATED
        and "get_current_user" in dependency_names.get((path, method.upper()), set())
        and "401" not in error_codes(op)
    ]
    assert not missing, f"depends on get_current_user but documents no 401:\n{failures(missing)}"


@pytest.mark.xfail(strict=True, reason="plan phase 3: documented 403")
def test_permission_guarded_operations_document_403(operations, dependency_names):
    guards = {"require_permission", "require_agent_tier"}
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if guards & dependency_names.get((path, method.upper()), set())
        and "403" not in error_codes(op)
    ]
    assert not missing, f"permission-guarded but documents no 403:\n{failures(missing)}"


@pytest.mark.xfail(strict=True, reason="plan phase 3: documented 404")
def test_operations_addressing_a_resource_document_404(operations):
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if "{" in path and "404" not in error_codes(op)
    ]
    assert not missing, f"addresses a resource by id but documents no 404:\n{failures(missing)}"


# --- Pagination and query parameters (plan phases 3 and 5) -----------------


@pytest.mark.xfail(strict=True, reason="plan phase 3: bounded limit")
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


@pytest.mark.xfail(strict=True, reason="plan phase 5: uniform collection envelope")
def test_unbounded_collections_return_the_standard_envelope(spec, operations):
    schemas = spec["components"]["schemas"]
    offenders = []
    for path, method, op in operations:
        if method != "get" or path.endswith("}") or path in BOUNDED_COLLECTIONS:
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
            offenders.append(f"GET {path} returns a bare array")
        elif "$ref" in body:
            name = body["$ref"].rsplit("/", 1)[-1]
            fields = set(schemas.get(name, {}).get("properties", {}))
            # RowsPageOut describes tabular query output, not a resource
            # collection, and is deliberately a different type.
            if "rows" in fields:
                continue
            if not {"items", "cursor", "has_more"} <= fields:
                offenders.append(f"GET {path} -> {name} {sorted(fields)}")
    assert not offenders, f"unbounded collection without the envelope:\n{failures(offenders)}"


# --- Identifiers (plan phase 4) --------------------------------------------


@pytest.mark.xfail(strict=True, reason="plan phase 4: path parameter renames")
def test_no_abbreviated_or_kind_naming_path_parameters(spec):
    banned = [
        f"{path} -> {{{param}}}"
        for path in spec["paths"]
        for _, param in path_params(path)
        if BANNED_PARAM_NAMES.match(param) and param not in PARAM_NAME_EXCEPTIONS
    ]
    assert not banned, f"abbreviated or kind-naming path parameter:\n{failures(banned)}"


@pytest.mark.xfail(strict=True, reason="plan phase 4: path parameter renames")
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


@pytest.mark.xfail(strict=True, reason="plan phase 4: no duplicated route families")
def test_no_route_is_served_at_two_paths(spec):
    """The default-catalog shim duplicates the catalog-scoped family. Two routes
    for one resource is the defect the plan exists to remove.
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


@pytest.mark.xfail(strict=True, reason="plan phase 5: attach and refresh-stats move")
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


# --- Errors and status codes (plan phase 5) --------------------------------


@pytest.mark.xfail(strict=True, reason="plan phase 5: single error envelope")
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


@pytest.mark.xfail(strict=True, reason="plan phase 5: Location on creation")
def test_creation_responses_declare_a_location_header(operations):
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in operations
        if "201" in op.get("responses", {})
        and "location" not in {h.lower() for h in op["responses"]["201"].get("headers", {})}
    ]
    assert not missing, f"201 without a Location header:\n{failures(missing)}"
