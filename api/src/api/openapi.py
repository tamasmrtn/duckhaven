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
