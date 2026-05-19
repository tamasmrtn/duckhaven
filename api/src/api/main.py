from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.routers import agents, agents_ws, auth, queries, workspaces
from api.routers.admin import agents as admin_agents
from api.routers.admin import audit as admin_audit
from api.routers.admin import storage as admin_storage

app = FastAPI(title="duckhaven-api")

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
app.include_router(queries.router, tags=["queries"])
app.include_router(agents.router, tags=["agents"])
app.include_router(agents_ws.router, tags=["agents"])
app.include_router(admin_agents.router, prefix="/admin", tags=["admin"])
app.include_router(admin_storage.router, prefix="/admin", tags=["admin"])
app.include_router(admin_audit.router, prefix="/admin", tags=["admin"])
