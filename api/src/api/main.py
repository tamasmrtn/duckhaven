from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from api.config import settings
from api.routers import agents, agents_ws, auth, queries, schemas, setup, workspaces
from api.routers.admin import agents as admin_agents
from api.routers.admin import audit as admin_audit
from api.routers.admin import storage as admin_storage
from api.services.uc_credentials import CredCache
from api.services.unity_catalog import UCClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.uc_client = UCClient(
        base_url=settings.uc_base_url,
        token=settings.uc_token,
        timeout_s=settings.uc_http_timeout_s,
    )
    app.state.cred_cache = CredCache(safety_window_s=settings.cred_safety_window_s)
    try:
        yield
    finally:
        await app.state.uc_client.aclose()


# The browser-facing REST API. Mounted under /api on the outer app so it shares
# an origin with the SPA; owns the lifespan-managed UCClient/CredCache state.
api_app = FastAPI(title="duckhaven-api", lifespan=lifespan)

api_app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_app.include_router(setup.router)
api_app.include_router(auth.router, prefix="/auth", tags=["auth"])
api_app.include_router(auth.me_router, tags=["auth"])
api_app.include_router(workspaces.router, tags=["workspaces"])
api_app.include_router(schemas.router, tags=["catalog"])
api_app.include_router(queries.router, tags=["queries"])
api_app.include_router(agents.router, tags=["agents"])
api_app.include_router(admin_agents.router, prefix="/admin", tags=["admin"])
api_app.include_router(admin_storage.router, prefix="/admin", tags=["admin"])
api_app.include_router(admin_audit.router, prefix="/admin", tags=["admin"])


class SPAStaticFiles(StaticFiles):
    """Serve index.html for any path that isn't a real static file, so the
    client-side router handles deep links and refreshes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except Exception:
            return await super().get_response("index.html", scope)


@asynccontextmanager
async def _outer_lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Starlette does not run a mounted sub-app's lifespan, so drive it here.
    async with api_app.router.lifespan_context(api_app):
        yield


# Outer ASGI app: agent WebSocket at root (agents dial /agents/connect), the
# REST API under /api, and the built SPA at / (only present in the image).
app = FastAPI(lifespan=_outer_lifespan)
app.include_router(agents_ws.router, tags=["agents"])
app.mount("/api", api_app)
if settings.static_dir.is_dir():
    app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="ui")
