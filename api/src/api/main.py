from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.routers import agents, agents_ws, auth, queries, schemas, workspaces
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


app = FastAPI(title="duckhaven-api", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(auth.me_router, tags=["auth"])
app.include_router(workspaces.router, tags=["workspaces"])
app.include_router(schemas.router, tags=["catalog"])
app.include_router(queries.router, tags=["queries"])
app.include_router(agents.router, tags=["agents"])
app.include_router(agents_ws.router, tags=["agents"])
app.include_router(admin_agents.router, prefix="/admin", tags=["admin"])
app.include_router(admin_storage.router, prefix="/admin", tags=["admin"])
app.include_router(admin_audit.router, prefix="/admin", tags=["admin"])
