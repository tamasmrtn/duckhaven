"""Schema-level conventions applied to every route at once.

The rules in docs/reference/api-conventions.md that can be derived rather than
hand-written are derived here, so they hold for endpoints that do not exist yet
and cannot drift as routes are added. api/tests/unit/test_openapi_conformance.py
asserts the result.
"""

from fastapi.routing import APIRoute

from api.schemas.error import ErrorOut

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

_ERROR_CONTENT = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorOut"}}}

_UNAUTHORIZED = {
    "description": "No valid session cookie or bearer token was supplied.",
    "content": _ERROR_CONTENT,
}
_FORBIDDEN = {
    "description": "Authenticated, but not permitted to perform this operation.",
    "content": _ERROR_CONTENT,
}
_NOT_FOUND = {
    "description": "No such resource, or the caller may not know it exists.",
    "content": _ERROR_CONTENT,
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
        components = schema.setdefault("components", {})
        components["securitySchemes"] = SECURITY_SCHEMES
        components.setdefault("schemas", {})["ErrorOut"] = ErrorOut.model_json_schema()

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
                # Every error body is ErrorOut, including the 422 FastAPI writes
                # from the request model and any a router declared by hand.
                for code, response in responses.items():
                    if code[0] in "45":
                        response["content"] = _ERROR_CONTENT
        return schema

    app.openapi = openapi


#: Status codes that carry a well-known machine code when a handler raises them
#: with a plain string detail. A handler that wants a more specific code raises a
#: dict detail carrying ``error``.
_DERIVED_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
    422: "unprocessable_content",
    502: "upstream_error",
    503: "unavailable",
    504: "upstream_timeout",
}


def error_body(status_code: int, detail: object) -> dict[str, object]:
    """Normalise any raised detail into the single error envelope.

    Handlers keep raising ``HTTPException`` as they always have. A dict detail
    carrying ``error`` maps straight through -- that is the shape the SQL guard
    and the semantic and session routers already use -- and a plain string
    becomes the message under a code derived from the status.
    """
    fallback = _DERIVED_ERROR_CODES.get(status_code, "internal_error")

    if isinstance(detail, dict):
        # The established shape is {"error": code, "detail": message}; anything
        # else in the dict is structured context worth keeping.
        code = detail.get("error") or fallback
        message = detail.get("detail") or detail.get("message") or fallback
        extra = {k: v for k, v in detail.items() if k not in ("error", "detail", "message")}
        return {"error": str(code), "message": str(message), "details": extra or None}

    if isinstance(detail, list):
        # FastAPI's request-validation errors: a list of per-field problems.
        return {
            "error": "validation_error",
            "message": "The request body or parameters did not validate.",
            "details": {"errors": detail},
        }

    return {"error": fallback, "message": str(detail) if detail else fallback, "details": None}
