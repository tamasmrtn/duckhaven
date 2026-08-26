"""Schema-level conventions applied to every route at once.

The rules in docs/reference/api-conventions.md that can be derived rather than
hand-written are derived here, so they hold for endpoints that do not exist yet
and cannot drift as routes are added. api/tests/unit/test_openapi_conformance.py
asserts the result.
"""

import inspect
import json
import re

from fastapi.routing import APIRoute

from api.schemas.error import ErrorOut


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
_CONFLICT = {
    "description": "The request conflicts with the resource's current state.",
    "content": _ERROR_CONTENT,
}
_UNAVAILABLE = {
    "description": "A dependency the request needs is not available right now.",
    "content": _ERROR_CONTENT,
}

#: Status constants a handler raises, and the response each implies. Read off the
#: endpoint's own source: which of these a route can return depends on its body,
#: not on its signature, so no dependency-tree inspection can find them and
#: hand-declaring 28 routes would drift the first time one changed.
_RAISED_IN_SOURCE = {
    "HTTP_409_CONFLICT": ("409", _CONFLICT),
    "HTTP_503_SERVICE_UNAVAILABLE": ("503", _UNAVAILABLE),
}


def _child_routes(route) -> list | None:
    """The routes an `include_router` wrapper stands for, or None if it is a leaf.

    `include_router` wraps children in a private `_IncludedRouter` whose
    `effective_candidates()` carry the combined path; the public
    `original_router.routes` still hold the unprefixed ones. Both names are
    FastAPI internals, so this degrades to treating the route as a leaf rather
    than letting a patch release turn schema generation into a 500.
    """
    candidates = getattr(route, "effective_candidates", None)
    if callable(candidates):
        try:
            return list(candidates())
        except Exception:  # noqa: BLE001 - any failure means "treat it as a leaf"
            return None
    return None


def _routes(collection):
    """Flatten the router tree to routes with their prefixes applied."""
    for route in collection:
        children = _child_routes(route)
        if children is not None:
            yield from _routes(children)
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


def _raised_codes(route) -> list[tuple[str, dict]]:
    """The 409/503 responses this route's own body can produce.

    Only direct raises in the endpoint function -- a helper it calls is invisible
    here, so this under-declares rather than over-declares. Degrades to nothing
    when the source is unavailable (a bytecode-only deployment), because a
    missing response line is better than a failed schema build.
    """
    try:
        source = inspect.getsource(route.endpoint)
    except OSError, TypeError:
        return []
    return [pair for name, pair in _RAISED_IN_SOURCE.items() if name in source]


def _guarantees(route) -> tuple[bool, bool, bool]:
    """Which of (401, 403, 404) this route can return.

    Derived from the guards the route actually declares rather than hand-written
    per endpoint, so a new route is documented correctly the moment it is added.
    """
    names = _dependency_names(route.dependant)
    authenticated = "get_current_user" in names
    # Every authenticated route can 403. Most authorization here runs *inside*
    # the handler -- assert_workspace_member, _load_session, _catalog_for_admin
    # all raise it -- so no dependency-tree inspection can find them, and a rule
    # that only saw the two dependency factories under-declared 16 operations
    # that demonstrably return 403.
    return authenticated, authenticated, "{" in route.path


def _prune_unreferenced(schema: dict) -> None:
    """Drop component schemas nothing points at any more.

    Replacing every error body with ErrorOut strands the models FastAPI
    generated for the responses it replaced. Left in place they are dead weight a
    client generator turns into real classes, so a consumer ends up with types
    for responses this API cannot send.
    """
    components = schema.get("components", {}).get("schemas", {})
    if not components:
        return
    # Iterate: pruning one schema can strand the ones only it referenced.
    while True:
        referenced = set(re.findall(r"#/components/schemas/([A-Za-z0-9_.-]+)", json.dumps(schema)))
        orphans = set(components) - referenced
        if not orphans:
            return
        for name in orphans:
            del components[name]


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
                for code, response in _raised_codes(route):
                    responses.setdefault(code, dict(response))
                # Every error body is ErrorOut. Total by design, including the
                # 422 FastAPI generates from the request model: the contract is
                # one error shape, so a route cannot opt out by declaring its
                # own. Anything left unreferenced by this is pruned below.
                for code, response in responses.items():
                    if code[0] in "45":
                        response["content"] = _ERROR_CONTENT

                # A route declaring a second success code (a PUT that creates,
                # a POST that can accept instead) states only its description;
                # the body is the response_model either way, so say so rather
                # than leaving the alternative looking empty.
                primary = next(
                    (r for c, r in responses.items() if c[0] == "2" and r.get("content")),
                    None,
                )
                if primary is not None:
                    for code, response in responses.items():
                        if code[0] == "2" and not response.get("content"):
                            response["content"] = primary["content"]

        _prune_unreferenced(schema)
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
    # An unmapped 4xx is still the caller's problem, so it must not derive a
    # code that says the server failed -- a 405 or a 429 reading `internal_error`
    # sends a client looking in the wrong place.
    fallback = _DERIVED_ERROR_CODES.get(
        status_code, "internal_error" if status_code >= 500 else "bad_request"
    )

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
