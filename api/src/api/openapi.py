"""Schema-level conventions applied to every route at once.

The rules in docs/reference/api-conventions.md that can be derived rather than
hand-written are derived here, so they hold for endpoints that do not exist yet
and cannot drift as routes are added. api/tests/unit/test_openapi_conformance.py
asserts the result.
"""

from fastapi.routing import APIRoute

#: Suffix for the deprecated default-catalog shim's operation ids. The shim
#: registers the same handlers as the canonical catalog-scoped routes, so their
#: names collide; the suffix disappears with the shim.
LEGACY_SUFFIX = "_default_catalog"


def operation_id(route: APIRoute) -> str:
    """Derive a stable ``operationId`` from the handler name.

    FastAPI's default embeds the path and the method, so it churns on every
    rename and reads badly as a generated-client method name
    (``list_agents_admin_agents_get``). The handler name is already the thing
    the route is called, so use it directly and keep handler names unique.
    """
    return route.name


#: Both credentials the API accepts, described once instead of as a cookie and a
#: header parameter on every operation. See ``deps.get_current_user``.
SECURITY_SCHEMES = {
    "cookieAuth": {
        "type": "apiKey",
        "in": "cookie",
        "name": "session",
        "description": "Browser session cookie, set by `POST /auth/login`.",
    },
    "bearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "description": "Service-account personal access token, for machine callers.",
    },
}

#: Either credential satisfies an authenticated route; Bearer wins when both are
#: sent. A list of single-key objects is OpenAPI's "any of these".
_EITHER_CREDENTIAL = [{"cookieAuth": []}, {"bearerAuth": []}]

_UNAUTHORIZED = {
    "description": "No valid session cookie or bearer token was supplied.",
}
_FORBIDDEN = {
    "description": "Authenticated, but not permitted to perform this operation.",
}
_NOT_FOUND = {
    "description": "No such resource, or the caller may not know it exists.",
}


def _routes(collection):
    """Flatten the router tree to routes with their prefixes applied."""
    from fastapi.routing import _IncludedRouter

    for route in collection:
        if isinstance(route, _IncludedRouter):
            yield from _routes(route.effective_candidates())
        elif hasattr(route, "dependant") and hasattr(route, "methods"):
            yield route


def _dependency_names(dependant) -> set[str]:
    """Every dependency callable a route resolves, by name.

    Dependency factories return a closure (``_dep``, ``_require``), so the
    factory's own name is taken from the qualname as well.
    """
    names: set[str] = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            names.add(getattr(sub.call, "__name__", ""))
            names.add(getattr(sub.call, "__qualname__", "").split(".")[0])
        names |= _dependency_names(sub)
    return names


def _guarantees(route) -> tuple[bool, bool, bool]:
    """Which of (401, 403, 404) this route can return.

    Derived from the guards the route actually declares rather than hand-written
    per endpoint, so a new route is documented correctly the moment it is added.
    """
    names = _dependency_names(route.dependant)
    authenticated = "get_current_user" in names
    forbiddable = bool(
        {"require_permission", "require_agent_tier"} & names
        # Workspace-scoped routes check membership inside the handler, which no
        # dependency can show; assert_workspace_member raises 403.
        or "/workspaces/{" in route.path
    )
    return authenticated, authenticated and forbiddable, "{" in route.path


def apply_conventions(app) -> None:
    """Fold the derivable schema conventions into ``app.openapi``.

    Wraps the app's own ``openapi()`` and mutates the result in place, so the
    cached schema FastAPI stores is the corrected one.
    """
    generate = app.openapi

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = generate()
        schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES

        by_operation = {
            (route.path, method): route for route in _routes(app.routes) for method in route.methods
        }
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                route = by_operation.get((path, method.upper()))
                if route is None:
                    continue
                authenticated, forbiddable, addressable = _guarantees(route)
                responses = operation.setdefault("responses", {})
                if authenticated:
                    operation.setdefault("security", _EITHER_CREDENTIAL)
                    responses.setdefault("401", dict(_UNAUTHORIZED))
                if forbiddable:
                    responses.setdefault("403", dict(_FORBIDDEN))
                if addressable:
                    responses.setdefault("404", dict(_NOT_FOUND))
        return schema

    app.openapi = openapi
