import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from api.config import settings
from api.db.session import async_session_factory
from api.metrics import PrometheusMiddleware
from api.openapi import apply_conventions, error_body, operation_id
from api.routers import (
    agents,
    agents_ws,
    assistant,
    auth,
    catalogs,
    grants,
    health,
    internal,
    lineage,
    maintenance,
    oidc,
    queries,
    schedules,
    schemas,
    search,
    semantic,
    setup,
    sql_sessions,
    workspaces,
)
from api.routers import (
    metrics as metrics_router,
)
from api.routers.admin import agent_access as admin_agent_access
from api.routers.admin import agents as admin_agents
from api.routers.admin import maintenance as admin_maintenance
from api.routers.admin import service_accounts as admin_service_accounts
from api.routers.admin import storage as admin_storage
from api.routers.admin import users as admin_users
from api.services.agent_dispatch import drain_local_agents
from api.services.assistant.identity import ASSISTANT_EMAIL
from api.services.bootstrap import ensure_assistant_service_account, seed_agent_bootstrap_token
from api.services.oidc import register_oidc
from api.services.polaris import (
    PolarisBadRequestError,
    PolarisClient,
    PolarisError,
    PolarisNotFoundError,
)
from api.services.rbac import seed_roles
from api.telemetry import setup_telemetry
from duckhaven_shared.telemetry import LOG_FORMAT, install_log_correlation


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn configures only its own loggers and leaves the root logger without
    # a handler, so module-level logs (scanner leadership, cross-replica dispatch
    # warnings, Polaris errors) would otherwise be dropped. Give the root logger a
    # handler; uvicorn's loggers don't propagate, so this won't double-log access.
    logging.basicConfig(level=settings.log_level, format=LOG_FORMAT)
    # basicConfig has no filter= param; attach trace_id/span_id correlation now.
    install_log_correlation()

    # Migrations have already run (api-entrypoint.sh) by the time the app
    # starts, so the credentials table exists. Seed before serving traffic so
    # the bundled agent can register the moment /api/healthz reports ready.
    async with async_session_factory() as db:
        await seed_roles(db)
        await seed_agent_bootstrap_token(
            db, settings.agent_bootstrap_token, settings.agent_bootstrap_ttl_hours
        )
        await ensure_assistant_service_account(db)

    # The assistant's account is fixed now. Settings ignores unknown env vars, so
    # a deployment still setting the old one would silently keep it and wonder why
    # its grants stopped applying.
    if os.environ.get("ASSISTANT_SERVICE_ACCOUNT_SLUG"):
        logging.getLogger(__name__).warning(
            "ASSISTANT_SERVICE_ACCOUNT_SLUG is no longer used; the assistant always "
            "acts as %s. Grant that account the workspace access you want it to have.",
            ASSISTANT_EMAIL,
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

    scheduler_task: asyncio.Task | None = None
    if settings.scheduler_enabled:
        from api.services.scheduler.scanner import scheduler_loop

        scheduler_task = asyncio.create_task(scheduler_loop(async_session_factory))

    migration_task: asyncio.Task | None = None
    if settings.migration_runner_enabled:
        from api.services.migration.runner import migration_loop

        migration_task = asyncio.create_task(
            migration_loop(async_session_factory, app.state.polaris_client)
        )

    reaper_task: asyncio.Task | None = None
    if settings.sql_sessions_enabled:
        from api.services.sql_sessions.reaper import reaper_loop

        reaper_task = asyncio.create_task(reaper_loop(async_session_factory))

    compute_reaper_task: asyncio.Task | None = None
    if settings.elastic_compute_enabled:
        from api.services.compute.reaper import reaper_loop as compute_reaper_loop

        compute_reaper_task = asyncio.create_task(compute_reaper_loop(async_session_factory))
    try:
        yield
    finally:
        # Stop reporting ready so the load balancer routes new work elsewhere,
        # then hand our agents to other replicas before tearing down.
        app.state.draining = True
        await drain_local_agents(async_session_factory)
        for task in (
            scanner_task,
            scheduler_task,
            migration_task,
            reaper_task,
            compute_reaper_task,
        ):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await app.state.polaris_client.aclose()


logger = logging.getLogger(__name__)

# The browser-facing REST API. Mounted under /api on the outer app so it shares
# an origin with the SPA; owns the lifespan-managed PolarisClient state.
api_app = FastAPI(
    title="duckhaven-api",
    version=settings.app_version,
    lifespan=lifespan,
    generate_unique_id_function=operation_id,
)

# Record request count/latency by route template (skips the /metrics scrape).
api_app.add_middleware(PrometheusMiddleware)

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


# Every 4xx and 5xx leaves through one of the three handlers below, so the body
# is the same shape whatever raised it. Handlers keep raising HTTPException as
# they always have; `error_body` normalises the detail they carry.
@api_app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.status_code, exc.detail),
        headers=getattr(exc, "headers", None),
    )


@api_app.exception_handler(RequestValidationError)
async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return JSONResponse(status_code=code, content=error_body(code, jsonable_encoder(exc.errors())))


@api_app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """The envelope has to hold for a crash too, or it is not a contract.

    Without this, an uncaught exception leaves through Starlette's own handler as
    `text/plain` "Internal Server Error" -- the one response a client cannot
    parse, arriving exactly when it most needs to know what happened. The
    exception is logged with its traceback and none of it reaches the caller.
    """
    logger.exception("Unhandled error serving %s %s", request.method, request.url.path)
    code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return JSONResponse(
        status_code=code,
        content=error_body(code, "The server failed to handle this request."),
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
    return JSONResponse(status_code=code, content=error_body(code, str(exc)))


api_app.include_router(health.router, tags=["health"])
api_app.include_router(metrics_router.router, tags=["metrics"])
api_app.include_router(setup.router)
api_app.include_router(auth.router, prefix="/auth", tags=["auth"])
api_app.include_router(auth.me_router, tags=["auth"])
api_app.include_router(oidc.router, prefix="/auth/oidc", tags=["auth"])
api_app.include_router(workspaces.router, tags=["workspaces"])
api_app.include_router(search.router, tags=["search"])
api_app.include_router(catalogs.router, tags=["catalog"])
api_app.include_router(schemas.router, tags=["catalog"])
api_app.include_router(grants.router, tags=["grants"])
api_app.include_router(lineage.router, tags=["lineage"])
api_app.include_router(semantic.router, tags=["semantic"])
api_app.include_router(queries.router, tags=["queries"])
api_app.include_router(sql_sessions.router, tags=["sql-sessions"])
api_app.include_router(schedules.router, tags=["schedules"])
api_app.include_router(agents.router, tags=["agents"])
api_app.include_router(assistant.router, tags=["assistant"])
api_app.include_router(maintenance.router, tags=["maintenance"])
api_app.include_router(admin_agents.router, prefix="/admin", tags=["admin-agents"])
api_app.include_router(admin_agent_access.router, prefix="/admin", tags=["admin-agents"])
api_app.include_router(admin_storage.router, prefix="/admin", tags=["admin-storage"])
api_app.include_router(admin_users.router, prefix="/admin", tags=["admin-users"])
api_app.include_router(
    admin_service_accounts.router, prefix="/admin", tags=["admin-service-accounts"]
)
api_app.include_router(admin_maintenance.router, prefix="/admin", tags=["admin-maintenance"])

# Security schemes and the 401/403/404 responses every guard implies, derived from
# the routes above rather than hand-declared per endpoint.
apply_conventions(api_app)


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
async def _outer_lifespan(outer: FastAPI) -> AsyncIterator[None]:
    # Starlette does not run a mounted sub-app's lifespan, so drive it here.
    async with api_app.router.lifespan_context(api_app):
        # The lifespan owns the client but sets it on the inner app, and the agent
        # WebSocket is mounted on this one — so anything the WS path needs has to
        # be reachable from here as well. Lineage extraction reads a source
        # table's columns through it when it cannot resolve them from the SQL.
        outer.state.polaris_client = api_app.state.polaris_client
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

# Instrument at module level: FastAPIInstrumentor injects middleware, which must
# be in place before the middleware stack is built at startup. Both apps need it
# — the outer app owns the agent WebSocket and /internal routes. No-op unless
# OTEL_EXPORTER_OTLP_ENDPOINT is set.
setup_telemetry(api_app, app)
