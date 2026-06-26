# REST API

The DuckHaven control plane is a FastAPI application. The web UI is a thin client over the same REST API documented
here, so anything the UI does can be scripted.

## Base path and interactive docs

The REST API is mounted under **`/api`** (it shares an origin with the SPA). FastAPI serves live, interactive
documentation generated from the running server:

- Swagger UI: `http://<host>:8000/api/docs`
- OpenAPI schema: `http://<host>:8000/api/openapi.json`

Because the schema is generated from the server, it always matches the deployed version — prefer it over any static
list.

## Resource groups

| Group | Covers |
|---|---|
| `auth` | Sign in / out, current user (`/api/auth/*`, `/api/me`) |
| `setup` | First-admin creation from the setup token |
| `workspaces` | Create and list workspaces and members |
| `catalog` | Catalogs (create/attach/detach/drop), schemas, tables, table detail, sample rows, snapshot history |
| `queries` | Submit queries, page result rows, profiles, saved queries, history |
| `agents` | List agents and capabilities |
| `admin` | Agents (bootstrap/revoke), storage backends, users, maintenance |

## Authentication

The API uses session-cookie authentication (see [Permissions](../concepts/permissions.md)). Authenticate as you would
in the browser and send the session cookie with each request.

!!! note "Private by design"
    DuckHaven has no public ingress; the API is reachable only on your private network (Tailscale recommended). The
    agent control channel is a separate WebSocket at `/agents/connect`, not part of this REST surface.
