import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from api.config import settings
from api.db.session import async_session_factory
from api.routers import (
    agents,
    agents_ws,
    auth,
    catalogs,
    health,
    internal,
    maintenance,
    oidc,
    queries,
    schemas,
    setup,
    workspaces,
)
from api.routers.admin import agents as admin_agents
from api.routers.admin import maintenance as admin_maintenance
from api.routers.admin import storage as admin_storage
from api.routers.admin import users as admin_users
from api.services.agent_dispatch import drain_local_agents
from api.services.bootstrap import seed_agent_bootstrap_token
from api.services.oidc import register_oidc
from api.services.polaris import (
    PolarisBadRequestError,
    PolarisClient,
    PolarisError,
    PolarisNotFoundError,
)
from api.services.rbac import seed_roles


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn configures only its own loggers and leaves the root logger without
    # a handler, so module-level logs (scanner leadership, cross-replica dispatch
    # warnings, Polaris errors) would otherwise be dropped. Give the root logger a
    # handler; uvicorn's loggers don't propagate, so this won't double-log access.
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Migrations have already run (api-entrypoint.sh) by the time the app
    # starts, so the credentials table exists. Seed before serving traffic so
    # the bundled agent can register the moment /api/healthz reports ready.
    async with async_session_factory() as db:
        await seed_roles(db)
        await seed_agent_bootstrap_token(
            db, settings.agent_bootstrap_token, settings.agent_bootstrap_ttl_hours
        )

    register_oidc()

    # Readiness gate: flipped on at the end of startup, off again at shutdown so a
    # load balancer drains this replica before it stops.
    app.state.draining = False

    app.state.polaris_client = PolarisClient(
        base_url=settings.polaris_base_url,
        realm=settings.polaris_realm,
        client_id=settings.polaris_client_id,
        client_secret=settings.polaris_client_secret,
        principal=settings.polaris_principal,
        timeout_s=settings.polaris_http_timeout_s,
    )

    scanner_task: asyncio.Task | None = None
    if settings.maintenance_scanner_enabled:
        from api.services.maintenance.scanner import scanner_loop

        scanner_task = asyncio.create_task(
            scanner_loop(async_session_factory, app.state.polaris_client)
        )
    try:
        yield
    finally:
        # Stop reporting ready so the load balancer routes new work elsewhere,
        # then hand our agents to other replicas before tearing down.
        app.state.draining = True
        await drain_local_agents(async_session_factory)
        if scanner_task is not None:
            scanner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scanner_task
        await app.state.polaris_client.aclose()


# The browser-facing REST API. Mounted under /api on the outer app so it shares
# an origin with the SPA; owns the lifespan-managed PolarisClient state.
api_app = FastAPI(title="duckhaven-api", lifespan=lifespan)

api_app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Short-lived signed cookie holding only the transient OIDC handshake state
# (state/nonce/PKCE verifier). Distinct from the app `session` cookie; this is
# the first real consumer of `secret_key`.
api_app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="dh_oidc",
    https_only=settings.cookie_secure,
    same_site="lax",
    max_age=600,
)


# Surface PolarisError escaping a route as a meaningful HTTP response instead of a
# bare 500. NotFound/BadRequest map to their natural client codes; everything else
# (server errors, conflicts, the base class) is an upstream failure -> 502.
@api_app.exception_handler(PolarisError)
async def _polaris_error_handler(_: Request, exc: PolarisError) -> JSONResponse:
    if isinstance(exc, PolarisNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, PolarisBadRequestError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code = status.HTTP_502_BAD_GATEWAY
    return JSONResponse(status_code=code, content={"detail": str(exc)})


api_app.include_router(health.router, tags=["health"])
api_app.include_router(setup.router)
api_app.include_router(auth.router, prefix="/auth", tags=["auth"])
api_app.include_router(auth.me_router, tags=["auth"])
api_app.include_router(oidc.router, prefix="/auth/oidc", tags=["auth"])
api_app.include_router(workspaces.router, tags=["workspaces"])
api_app.include_router(catalogs.router, tags=["catalog"])
api_app.include_router(schemas.router, tags=["catalog"])
api_app.include_router(queries.router, tags=["queries"])
api_app.include_router(agents.router, tags=["agents"])
api_app.include_router(maintenance.router, tags=["maintenance"])
api_app.include_router(admin_agents.router, prefix="/admin", tags=["admin"])
api_app.include_router(admin_storage.router, prefix="/admin", tags=["admin"])
api_app.include_router(admin_users.router, prefix="/admin", tags=["admin"])
api_app.include_router(admin_maintenance.router, prefix="/admin", tags=["admin"])


class SPAStaticFiles(StaticFiles):
    """Serve index.html for client-side routes, so the router handles deep links
    and refreshes. Missing files that look like assets (anything with a file
    extension) return a real 404 instead of HTML, so broken asset URLs surface
    as errors rather than being masked as an HTTP 200 index.html."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            last_segment = path.rsplit("/", 1)[-1]
            if exc.status_code == 404 and "." not in last_segment:
                return await super().get_response("index.html", scope)
            raise


@asynccontextmanager
async def _outer_lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Starlette does not run a mounted sub-app's lifespan, so drive it here.
    async with api_app.router.lifespan_context(api_app):
        yield


# Outer ASGI app: agent WebSocket at root (agents dial /agents/connect), the
# REST API under /api, and the built SPA at / (only present in the image).
app = FastAPI(lifespan=_outer_lifespan)
app.include_router(agents_ws.router, tags=["agents"])
# Network-private inter-replica dispatch; never exposed past the internal network.
app.include_router(internal.router)
app.mount("/api", api_app)
if settings.static_dir.is_dir():
    app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="ui")
