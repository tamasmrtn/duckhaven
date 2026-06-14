# DuckHaven — Architecture

This document is the map of the DuckHaven codebase. It explains what the
system is, how it is organized, how data moves through it, and — most
importantly — **where to make a change**. It is written to be read in one
sitting by a new contributor or a coding agent before touching the code.

It deliberately describes *stable structure and invariants*, not progress.
For roadmap and milestone status see the [README](../README.md#roadmap) and
the issue tracker. For setup and operations see
[DEVELOPMENT.md](DEVELOPMENT.md), [RUNBOOK.md](RUNBOOK.md), and
[self-hosting/](self-hosting/).

---

## 1. Overview

DuckHaven is a **self-hosted, browser-based SQL workspace** for teams that
run [DuckDB](https://duckdb.org/) over Apache Iceberg tables governed by
[Apache Polaris](https://polaris.apache.org/). It gives a small team
(2–10 users) collaborative worksheets, a shared catalog, per-workspace
permissions, and a full audit trail — without a cloud warehouse, Kubernetes,
or a platform team.

Architecturally, DuckHaven is a **control plane / compute split**:

- The **control plane** (`api/`) is a single FastAPI process. It owns
  identity, workspaces, the catalog/DDL, query state, and the agent
  registry. **It never runs DuckDB queries.**
- **Compute** lives in one or more **agents** (`agent/`). Each agent embeds
  a DuckDB engine, runs on its own host, and *dials home* to the control
  plane over a WebSocket. Users pick which agent runs each query.

The result is a system that is small at the center (a Docker Compose stack
on one homelab-class box) and horizontally expandable at the edge (add an
agent host when you need more compute).

| Concern | Choice |
|---|---|
| Control plane | One `docker compose` stack: Postgres + Apache Polaris + the API |
| Compute | 1..N DuckDB **agents** on separate hosts |
| Engines | DuckDB only (heterogeneous versions allowed) |
| Storage | Apache Iceberg on Object storage (bundled MinIO) / S3 / ADLS Gen 2 (one backend per workspace) |
| Catalog & credentials | Apache Polaris — table governance + short-lived credential vending |
| Frontend | React SPA — SQL worksheets (no notebooks) |
| Network | Private only (Tailscale recommended); no public ingress |

---

## 2. Purpose & Philosophy

**Why DuckHaven exists.** Teams that love DuckDB end up sharing `.duckdb`
files over chat. DuckHaven provides the worksheet/collaboration experience
of MotherDuck or Databricks while keeping data on your own infrastructure,
with no SaaS lock-in and no opaque billing.

Two ideas shape nearly every design decision:

1. **DuckHaven is a dispatcher, not an optimizer.** The user picks the
   engine (agent) per worksheet. There is no distributed query planner and
   no cost-based routing. Compute is transparent and explicit.
2. **A workspace is bound to exactly one storage backend.** This binding is
   chosen at workspace-create time, is immutable, and is enforced on every
   write. It keeps governance, credentials, and disaster-recovery reasoning
   simple.

### Non-goals (explicit boundaries)

- **Not a Spark/Databricks replacement.** No distributed query plan; agents
  are independent DuckDB processes with no cross-agent atomicity.
- **Not multi-engine (yet).** DuckDB only. The agent contract is drawn so a
  second engine type can be added without re-architecting the control plane.
- **Not a notebook platform.** SQL worksheets only.
- **Not internet-exposed.** The private network (Tailscale/WireGuard) is the
  security perimeter; the API speaks plain HTTP behind it.
- **Not authoritative storage and not an ingestion engine.** Source data
  lives in the backends; external tools (PyIceberg, dlt, Spark) write it.
- **No cross-workspace joins, no row/column security** in the current scope.
  Permissions are workspace-level. (DDL and destructive DML — `CREATE`/`ALTER`/
  `DROP`, `UPDATE`/`DELETE`/`MERGE` — *are* supported; see Invariant I8.)

---

## 3. High-Level Architecture

```mermaid
flowchart TB
    subgraph client[Client]
        Browser["React SPA<br/>(browser, private network)"]
    end

    subgraph cp[Control plane — one Docker Compose stack]
        API["duckhaven-api (FastAPI)<br/>auth · workspaces · queries<br/>DDL · agent registry · audit"]
        PG[("Postgres 16<br/>app state + Polaris metastore")]
        Polaris["Apache Polaris<br/>catalog + credential vendor"]
    end

    subgraph edge[Compute edge — separate hosts]
        A1["duckhaven-agent<br/>DuckDB engine"]
        A2["duckhaven-agent<br/>DuckDB engine"]
    end

    subgraph store[Storage backends — one per workspace]
        S[("Object storage / S3 / ADLS Gen 2<br/>Apache Iceberg tables")]
    end

    Browser -- "HTTPS-over-tunnel<br/>/api/*" --> API
    API -- SQLAlchemy --> PG
    API -- REST --> Polaris
    A1 -. "outbound WebSocket<br/>(agent dials home)" .-> API
    A2 -. "outbound WebSocket" .-> API
    API -- "HTTP read<br/>(result Parquet → JSON)" --> A1
    A1 -- "short-lived creds" --> S
    A2 --> S
```

The defining structural fact: **the only long-lived connection between the
control plane and an agent is initiated *by the agent*** (the WebSocket
control channel). The control plane reaches back to an agent in exactly one
place — an HTTP `GET` to fetch the result Parquet, which the API decodes to
JSON rows. Everything else flows over the agent-initiated socket.

---

## 4. Core Architectural Principles

1. **Separation of control and compute.** The control plane orchestrates;
   agents execute. The control plane process never opens a DuckDB database
   (it uses DuckDB *only as a SQL parser* — see Invariant I1).
2. **Agents are cattle that dial home.** An agent needs only a control-plane
   URL and a bootstrap token. It registers itself, advertises its
   capabilities, and holds one socket open. The control plane keeps no
   static inventory of agent addresses.
3. **Apache Polaris is the source of truth for catalog structure.** Schemas,
   tables, columns, and table properties live in Polaris, not in Postgres. DuckHaven
   never shadows catalog *structure* in its own database — it only keeps a
   supplementary `table_metadata` sidecar for facts Polaris does not track
   (ownership, last-write provenance, row/size stats).
4. **Postgres is the single state-of-record for everything DuckHaven owns**
   (users, workspaces, queries, agents). There is no Redis or separate queue
   — query dispatch is a direct push over the agent socket.
5. **Credentials are short-lived and connection-scoped.** Polaris vends temporary
   storage credentials per `(agent, workspace)`; the agent applies them as a
   DuckDB `SECRET` that dies with the per-query connection.
6. **The wire contract is shared, not duplicated.** The control↔agent frame
   protocol lives in one package (`shared/`) imported by both sides, so it
   cannot drift.

---

## 5. Repository Structure

DuckHaven is a polyglot monorepo: a `uv` Python workspace (`api`, `agent`,
`shared`) plus an npm React app (`web`).

```
duckhaven/
├── api/          FastAPI control plane (the "brain")
├── agent/        DuckDB compute agent (the "muscle")
├── shared/       duckhaven-shared: the control↔agent wire contract
├── web/          React SPA (SQL worksheets, catalog, admin)
├── deploy/       Docker Compose stack, entrypoints, secrets bootstrap
├── scripts/      Operator helpers (pg backup, token generation)
├── docs/         This file + development, runbook, self-hosting guides
├── Makefile      Canonical dev/test/lint/migrate/compose commands
└── pyproject.toml  uv workspace root (members: api, agent, shared)
```

Each Python package owns its own `pyproject.toml`. `api` and `agent` both
declare `duckhaven-shared = { workspace = true }`. The dependency direction
is strict: **`api` and `agent` depend on `shared`; nothing depends on `api`
or `agent`** (Invariant I6).

---

## 6. Code Map

This is the navigation core. For each package: what it is for, how its
internals are organized, and the files to open first.

### 6.1 `shared/` — `duckhaven_shared` (the contract)

**Purpose.** The single source of truth for the control-plane ↔ agent wire
format. Tiny by design (only depends on `pydantic`).

| Module | Defines |
|---|---|
| `protocol.py` | `FrameType` (string enum) and `Frame` (`{type, payload}`) — every message on the control WebSocket |
| `schemas.py` | `AgentCapabilities` — the document an agent advertises (DuckDB version, loaded extensions, memory ceiling, cores, host) |

`FrameType` values: `auth`, `auth_ok`, `dispatch_query`, `query_progress`,
`query_done`, `cancel_query`, `heartbeat`, `agent_status`. **Adding or
changing a frame means editing this package** — both sides pick it up by
import, which is how the contract stays in sync (Invariant I5).

### 6.2 `api/` — `duckhaven-api` (control plane)

**Purpose.** Everything the browser talks to, plus the agent-facing
WebSocket. A FastAPI app backed by async SQLAlchemy + Postgres.

**App composition** (`src/api/main.py`) — important to understand the URL
surface:

- An **outer** ASGI app mounts three things:
   - the **agent WebSocket** at `/agents/connect` (root level — agents dial here),
   - the **REST API** sub-app under **`/api`** (shares an origin with the SPA),
   - the **built SPA** as static files at `/` (only present in the image; a
     catch-all serves `index.html` so client-side routes deep-link).
- The REST sub-app owns a lifespan that constructs the shared `PolarisClient`
  (held on `app.state`).

**Internal layout:**

| Directory | Responsibility |
|---|---|
| `routers/` | HTTP/WS endpoints. One module per resource: `auth`, `workspaces`, `schemas` (catalog DDL + table sample), `queries`, `agents`, `health`, `setup`, plus `agents_ws` (the agent WebSocket) and `admin/` (`agents`, `storage`, `audit`). |
| `services/` | Business logic, framework-free. The interesting code lives here (see below). |
| `models/` | SQLAlchemy ORM models = the Postgres schema. |
| `schemas/` | Pydantic request/response DTOs (distinct from ORM models). |
| `db/` | Engine/session setup (`session.py`) and the declarative `Base`. |
| `deps.py` | FastAPI dependencies: `get_db`, `get_current_user`, `get_polaris_client`. |
| `config.py` | `pydantic-settings` config (DB URL, Polaris URL + client credentials, cookie/secret settings). |
| `alembic/` | Database migrations. Applied automatically by the container entrypoint in production. |

**Key services to know:**

| Service | What it does | Interacts with |
|---|---|---|
| `services/query.py` | Dispatches a query to an agent over the registry socket; handles `query_progress`/`query_done` frames coming back; fetches the result Parquet from the agent and decodes it to JSON rows (`decode_parquet_page`); also drives the synchronous table-sample preview and persists agent-reported table stats. **The heart of the system.** | `agent_registry`, the `Query`/`TableMetadata` models |
| `services/agent_registry.py` | In-memory `ConnectionManager` (`registry`) mapping `agent_id → live WebSocket`. The only place that knows which agents are connected *right now*. | `routers/agents_ws.py`, `services/query.py` |
| `services/polaris.py` | Async client for Polaris: catalogs (management API) + namespaces/tables (Iceberg REST), with OAuth2 client-credentials auth. Speaks REST directly via `httpx`. | Polaris container |
| `services/sql_guard.py` | The SQL allowlist. Uses `duckdb.extract_statements` to **parse only** and allow data + catalog DDL statements (`SELECT`/`INSERT`/`UPDATE`/`DELETE`/`MERGE`/`CREATE`/`ALTER`/`DROP`), rejecting sandbox-escaping ones (`ATTACH`, `COPY`, `LOAD`, `SET`, …). No connection, no execution. | `routers/queries.py` |
| `services/agent_capabilities.py` | Maps a backend kind to its required DuckDB extension and checks an agent's advertised capabilities at dispatch time. | `routers/queries.py` |
| `services/workspace.py` | Membership/role checks (`assert_workspace_member`), workspace lookup, lazy Polaris catalog creation (`ensure_polaris_catalog`), backend→storage mapping (`polaris_storage`). | Polaris, the `Workspace`/`WorkspaceMember` models |
| `services/auth.py` | bcrypt password hashing/verification and session-cookie handling. | `routers/auth.py`, `routers/setup.py` |

### 6.3 `agent/` — `duckhaven-agent` (compute)

**Purpose.** Embed a DuckDB engine, execute dispatched queries, and serve
result files. An agent is a single Python process running three concurrent
tasks (`src/agent/main.py` gathers them):

1. **Control channel** (`control/channel.py`) — opens the outbound WebSocket,
   authenticates, advertises `AgentCapabilities`, then loops handling frames
   (`dispatch_query`, `cancel_query`, `heartbeat`, `set_concurrency`). Reconnects
   with backoff if the socket drops. On each heartbeat it re-advertises
   capabilities; each `metrics_sample` also carries the live running/queued query
   counts and active concurrency profile.
2. **Result server** (`results/server.py`) — an HTTP server bound to
   `RESULTS_HTTP_HOST` (`0.0.0.0` in compose) that advertises its port to the
   control plane, serving `results/{query_id}.parquet` with **HTTP `Range`**
   support, gated
   by a Bearer token (the agent's session token, held in `auth.py:TokenHolder`).
3. **Retention sweep** (`results/retention.py`) — periodically deletes result
   Parquet files older than the retention window.

**Query execution** is split for testability and cancellation:

| Module | Responsibility |
|---|---|
| `executor/admission.py` | `Admission`: memory-budget admission control with a FIFO queue. The cgroup-aware budget (`effective_memory_bytes() × (1 − headroom)`) is split into a weighted slot ladder (profiles in `duckhaven_shared.concurrency`); a new query takes the largest free slot, the rest queue. In the default `auto` profile it instead admits a per-query *requested* reservation (sized from the estimate) clamped to `[floor, ceiling×budget]`. Enforces the invariant `Σ running memory_limit ≤ budget` so the agent never OOM-kills. Exposes live running/queued counts and the active profile, switchable at runtime via the `set_concurrency` frame. |
| `executor/plan.py` | Shared DuckDB plan/profile tree-walker: one recursive walker, two extractors. `parse_explain` reads estimated cardinality from `EXPLAIN (FORMAT json)` (pre-execution); `parse_profile` reads actual per-operator metrics + a query-level summary from the JSON profile (post-execution). Consumed by both the estimator and the profiler. |
| `executor/estimator.py` | `estimate_memory_bytes`: runs `EXPLAIN` on the attached connection and approximates peak memory as `Σ` over blocking operators (joins, group-bys, sorts) of `estimated_cardinality × row_width × safety`; `bucket_for` snaps it to a T-shirt bucket of the budget. Best-effort — returns `None` (caller uses the fallback bucket) on any failure. |
| `executor/runner.py` | `run_query_sync`: the synchronous DuckDB path. Sets `memory_limit`/`threads` to the slice the admission manager granted, loads `iceberg` (+ `httpfs`/`azure` for cloud), creates a connection-scoped iceberg OAuth2 `SECRET` from agent config and `ATTACH`es the workspace's Polaris catalog (warehouse = workspace slug; `vended_credentials` for every backend, since all are object storage), then `COPY (sql) TO '<uuid>.parquet'`. Around execution it enables DuckDB JSON profiling and normalizes the result into `stats["profile"]` (best-effort; the `auto` path reuses the connection it ran `EXPLAIN` on). When the dispatch carries `stats_for`, it also returns the target table's `COUNT(*)` for the catalog sidecar. |
| `executor/supervisor.py` | `run_query`: runs `run_query_sync` on a thread executor with a wall-clock timeout. Uses DuckDB's thread-safe `conn.interrupt()` (via `loop.call_later` and on cancel) to actually stop a running query. |

`config.py` holds the operator timeout ceiling (`max_timeout_s`, per-query
requests clamp to it), the admission knobs (`max_concurrency_profile` — default
`auto` — `memory_headroom_fraction`, `max_queue_depth`, `queued_timeout_s`), the
estimator knobs (`estimate_safety_multiplier`, `estimate_floor_bytes`,
`estimate_ceiling_fraction`, `explain_timeout_s`, `estimate_fallback_bucket`),
and the profiling kill-switch (`profiling_enabled`, default on).

### 6.4 `web/` — the React SPA

**Purpose.** The worksheet UI and admin console. React 19 + Vite, TanStack
Router/Query/Table, Monaco editor, Radix + shadcn/ui + Tailwind.

**Layered by responsibility** (open in this order to trace a feature):

| Directory | Responsibility |
|---|---|
| `src/api/` | Thin `fetch` wrappers. `client.ts` prefixes every call with `/api` and sends cookies; one module per resource. |
| `src/queries/` | TanStack Query hooks (`useX` query/mutation) wrapping `src/api/`. The data-fetching seam components consume. |
| `src/features/` | Page-level features: `worksheet/`, `catalog/`, `saved-queries/`, `history/`, `auth/`, `admin/`. |
| `src/components/app/` | App-shell chrome (`AppShell`, `TopBar`, `LeftRail`, `AgentPicker`, `WorkspaceSwitcher`, `CommandPalette`). |
| `src/components/ui/` | shadcn/ui primitives. |
| `src/router.tsx` | TanStack route tree. Authed routes nest under `/$ws/...` (`worksheets`, `catalog`, `saved-queries`, `history`, `queries/$queryId` (the dedicated query-profile graph), `admin/*`). |
| `src/types/` | Shared TypeScript types mirroring the API DTOs. |
| `src/mock/` | MSW handlers + fixtures used in `dev` (and tests) when the real API is absent. |

The frontend's `AgentPicker` runs the same backend-compatibility check the
control plane enforces server-side, so incompatible agents are visibly
disabled before a query is even sent.

### 6.5 `deploy/` & `scripts/`

`deploy/docker-compose.yml` defines the all-in-one stack:
`postgres` → `polaris-bootstrap` (one-shot schema + root principal) →
`polaris` → `api` → `agent`, plus `minio` for object storage.
`api-entrypoint.sh` generates the first-boot secrets (including the one-shot
admin setup token), runs Alembic migrations, then starts uvicorn; the API
also seeds the agent bootstrap token on startup. `minio` pre-creates the
warehouse bucket in its own entrypoint. Remote agents can still be deployed
per host against the same control plane. `scripts/` holds operator helpers
(`pg-backup.sh`, `gen-token.sh`).

---

## 7. Data Model

DuckHaven splits its state across two stores, and the split is itself an
invariant (I3): **DuckHaven's own entities live in Postgres; catalog
metadata lives in Apache Polaris.**

### Postgres (owned by `api/src/api/models/`)

```mermaid
erDiagram
    users ||--o{ credentials : has
    users ||--o{ workspace_members : joins
    workspaces ||--o{ workspace_members : has
    workspaces ||--o{ queries : runs
    workspaces ||--o{ saved_queries : stores
    workspaces ||--o{ table_metadata : "stats + ownership"
    workspaces }o--|| storage_backends : "pinned to (1)"
    agents ||--o{ credentials : "session token"
    agents ||--o{ queries : executes

    users {
        uuid id
        string email
        string password_hash
        string role
    }
    credentials {
        uuid id
        uuid user_id
        uuid agent_id
        string kind
        string token
        datetime expires_at
    }
    workspaces {
        uuid id
        string slug
        uuid storage_backend_id
    }
    workspace_members {
        uuid workspace_id
        uuid user_id
        string role
    }
    storage_backends {
        uuid id
        string kind
        string name
        string root_uri
    }
    agents {
        uuid id
        string name
        string status
        json capabilities
        string result_host
        int result_port
    }
    queries {
        uuid id
        uuid workspace_id
        uuid agent_id
        uuid user_id
        text sql
        string status
        string origin
        int row_count
        json progress
        string result_path
        jsonb profile
    }
    saved_queries {
        uuid id
        uuid workspace_id
        text sql
        uuid default_agent_id
    }
    table_metadata {
        uuid id
        uuid workspace_id
        string schema_name
        string table_name
        uuid owner_id
        bigint row_count
        bigint size_bytes
        datetime last_write_at
    }
```

Notes that matter for changes:

- **`credentials` is polymorphic** by `kind`: user `session`, agent
  `agent_bootstrap` (single-use), and agent `agent_session` (long-lived).
- **`agents.capabilities`** is the last advertised `AgentCapabilities` JSON;
  `result_host`/`result_port` tell the API where to fetch the result Parquet.
- **`queries` is also the audit log** — there is no separate audit table.
  `GET /admin/audit` reads filtered rows straight from `queries`, excluding
  internal rows (`origin = "sample"`, used by the table-sample preview).
- **`table_metadata` is the catalog sidecar** — the only DuckHaven-owned table
  keyed by a Polaris schema/table name. It holds what Polaris does not track: `owner_id`,
  `last_write_*`, and agent-computed `row_count`/`size_bytes`. Populated on
  table create and on sample/stats completion; merged into `TableOut` by
  `routers/schemas.py`. Polaris remains the source of truth for catalog structure.
- **`workspaces.storage_backend_id` is immutable** after creation, and a
  backend cannot be deleted while any workspace references it.

### Apache Polaris (owned by Polaris, addressed via `services/polaris.py`)

One **Polaris catalog per workspace** (named by the workspace `slug`), containing
namespaces (schemas) and tables. The default namespace is **`analytics`**, not
`main`: `main` is DuckDB's built-in default schema in an attached catalog and
shadows the Iceberg namespace, so `catalog.main.table` won't resolve. Every
DuckHaven-created table is Apache **Iceberg** format and catalog-managed by
definition; its location sits under the catalog's base location, derived from
the workspace backend's `root_uri`. Catalogs are created **fully DuckHaven-owned**:
`ensure_catalog_access` grants the service principal the full catalog-management
set (`CATALOG_MANAGE_CONTENT` + `CATALOG_MANAGE_METADATA` + `CATALOG_MANAGE_ACCESS`),
and `create_catalog` enables `polaris.config.drop-with-purge.enabled` so `DROP`
reclaims data files — without the content grant Polaris returns 403 on table data
access. REST-created tables record a `table_metadata` owner sidecar at create time;
tables created via SQL DDL get one lazily (stats on sample). A table's **Iceberg
snapshot history** (the catalog History tab) is read **live** off the
`loadTable` metadata via `list_snapshots` — never persisted to Postgres — and a
"query at this snapshot" worksheet pins the read with DuckDB's `AT (VERSION => …)`
/ `AT (TIMESTAMP => …)` time-travel clause (there is no `BEFORE`; the UI says
"as of"). The agent's DuckDB
sets the working catalog with `USE <catalog>.<schema>`
(a bare `USE <catalog>` does not resolve the attached REST catalog). Polaris
vends short-lived, scoped storage credentials to DuckDB on attach via access
delegation (`ACCESS_DELEGATION_MODE 'vended_credentials'`).

**Storage policy — object storage only (MinIO bundled).** Every workspace
catalog is backed by **S3-compatible object storage**; there is no Polaris FILE
storage. This is forced by DuckHaven's control-plane/compute-split topology:
DuckDB can only read **and write** Iceberg tables through the REST catalog when
storage is S3-compatible, because Polaris must vend scoped credentials the
remote agent uses. FILE storage cannot support writes across the
Polaris-container / remote-agent boundary (Polaris creates table directories as
its container user; the agent gets permission denied), so it was removed. The
compose stack therefore **bundles MinIO**, and the `object_store` backend
kind is physically backed by a MinIO bucket: its catalogs use
`storageType = S3` pointed at MinIO (with the catalog's vended `endpoint` set to
an externally-reachable URL the agent can reach, and an internal endpoint for
Polaris itself). Per-workspace isolation comes from a `/{slug}` prefix under the
shared bucket. The `s3`/`adls_gen2` kinds remain operator-owned external object
stores.

---

## 8. Data Flow & Runtime Behavior

### 8.1 Query lifecycle (the primary flow)

```mermaid
sequenceDiagram
    participant UI as React SPA
    participant API as duckhaven-api
    participant PG as Postgres
    participant Polaris as Apache Polaris
    participant AG as Agent (DuckDB)
    participant ST as Storage backend

    UI->>API: POST /api/workspaces/{ws}/queries {sql, agent_id}
    API->>API: auth + membership check
    API->>API: sql_guard.assert_allowed (parse-only)
    API->>API: agent connected? backend compatible?
    API->>PG: insert query (status=queued)
    API->>AG: dispatch_query frame (sql, backend, workspace slug) [over agent WS]
    API->>PG: status=running
    API-->>UI: 202 {id, status}

    AG->>AG: SET memory_limit, CREATE iceberg SECRET (from config), ATTACH Polaris catalog
    AG->>Polaris: load table metadata + vended storage creds (cloud) on attach
    AG->>ST: COPY (sql) TO results/{id}.parquet
    AG->>API: query_done frame {row_count, duration_ms, result_path}
    API->>PG: update query (status=done, ...)

    UI->>API: GET /api/queries/{id} (poll)
    UI->>API: GET /api/queries/{id}/rows?limit&cursor
    API->>AG: GET /results/{id}.parquet (Bearer session token)
    AG-->>API: parquet bytes
    API->>API: decode_parquet_page (duckdb read_parquet, LIMIT/OFFSET)
    API-->>UI: RowsPageOut JSON {rows, columns, cursor, total}
```

Key properties:

- **Dispatch is a direct socket push**, not a queue. If the chosen agent is
  not connected, the request fails fast (`503`).
- **The reservation is sized before execution** (default `auto` profile). The
  agent runs `EXPLAIN` on the attached connection, estimates peak memory, and
  acquires a proportional reservation (queueing if the budget is full) — then
  reuses that same connection to execute and read the profile.
- **The execution profile is captured after the run.** The agent normalizes
  DuckDB's JSON profile (query summary + operator tree) and returns it on the
  `query_done` frame; the API persists it on `Query.profile` and serves it from
  `GET /queries/{id}/profile` for the worksheet's Profile tab. Best-effort, so a
  profiling failure never fails the query.
- **Results are materialized where they are produced** — Parquet on the
  executing agent. The control plane fetches that Parquet and decodes the
  requested page to JSON (`RowsPageOut`) with `duckdb`; `total` comes from the
  persisted `Query.row_count`. Result lifetime is bounded by the agent's
  retention sweep, so a stale query is simply re-run from its saved SQL.
- **Cancellation** sends a `cancel_query` frame; the agent calls
  `conn.interrupt()` to stop the in-flight DuckDB query.
- **A timeout** is enforced agent-side by the supervisor, also via
  `conn.interrupt()`.

### 8.2 Agent connection lifecycle

```mermaid
sequenceDiagram
    participant AG as Agent
    participant API as duckhaven-api
    participant PG as Postgres

    AG->>API: connect ws:/agents/connect
    AG->>API: auth frame {bootstrap_token}
    API->>PG: validate + delete single-use bootstrap cred
    API->>PG: create agent row + agent_session credential
    API-->>AG: auth_ok {agent_id, session_token}
    AG->>API: agent_status {capabilities}
    API->>PG: store capabilities, status=healthy
    loop while connected
        API->>AG: heartbeat
        AG->>API: heartbeat + agent_status (re-advertise)
        API->>AG: dispatch_query / cancel_query (as needed)
    end
    Note over API,PG: on disconnect → status=unavailable, drop from registry
```

The bootstrap token is exchanged exactly once for a long-lived
`agent_session` token. That session token is what the control plane later
presents as a Bearer credential when reading result rows.

---

## 9. External Integrations

| Integration | Role | Boundary in code |
|---|---|---|
| **DuckDB** | The query engine — present *only* on agents. Also used by the control plane as a pure SQL parser. | `agent/.../executor/`, `api/.../services/sql_guard.py` |
| **Apache Polaris** | Iceberg REST catalog: metadata authority + vendor of short-lived storage credentials (via access delegation). | `api/.../services/polaris.py` |
| **Storage backends** | Where Iceberg tables physically live (all object storage): `object_store` (bundled MinIO, `httpfs`), S3 (`httpfs`), ADLS Gen 2 (`azure`). One per workspace. | `agent/.../executor/runner.py` (iceberg attach), `StorageBackend` model |
| **Postgres** | State-of-record for DuckHaven entities + the Polaris metastore. | `api/.../db/`, `models/` |
| **Tailscale (operational)** | Recommended private network providing the transport-layer security perimeter. Not a code dependency. | deployment only |

---

## 10. Deployment Architecture

**All-in-one Docker Compose stack** (`deploy/docker-compose.yml`) — six
services:

```
postgres:16-alpine
minio                (object store; publishes :9000 API, :9001 console)
polaris-bootstrap →  apache/polaris:latest  (one-shot realm/principal; storage: S3 → MinIO)
                     duckhaven-api    (publishes :8000, serves SPA + REST + agent WS)
                     duckhaven-agent  (bundled compute; dials the API WS)
```

`polaris-bootstrap` is the only remaining one-shot — it provisions the Polaris
realm/principal (the admin tool ships as its own image). Everything else
self-prepares: `api-entrypoint.sh` generates the secret key + setup token on
first boot and applies migrations; the API seeds the agent bootstrap token on
startup; `minio` pre-creates the warehouse bucket in its entrypoint; Postgres
creates the dedicated `polaris` DB via an initdb script. MinIO's `:9000`
endpoint must be reachable by remote agents (the URL Polaris vends to DuckDB),
so it is published and configured via `S3_ENDPOINT` (default `http://minio:9000`
for the bundled agent). `api` is published directly on `:8000` over the private
network — there is no edge TLS terminator by default; transport security comes
from the tunnel. Images are built for `linux/amd64,linux/arm64` and published to
`ghcr.io/tamasmrtn/duckhaven-{api,agent}`.

**Additional agents — one process per host**, deployed separately against the
same control plane. An agent needs only the control-plane WebSocket URL and a
bootstrap token. It writes results and mounts under `/var/duckhaven-agent/`.

```
/var/duckhaven-agent/
  results/{query_uuid}.parquet   # materialized results (swept on a timer)
  cache/                         # optional DuckDB object-store cache
  mounts/                        # operator-configured NAS/FS mounts
```

---

## 11. Architectural Invariants

These are the rules that keep the design coherent. **A change that violates
one of these is almost certainly wrong** — if you believe you need to, raise
it explicitly rather than working around it.

- **I1 — The control plane never executes user SQL.** `api/` may construct a
  DuckDB object only to parse (`sql_guard`) and to decode a result Parquet
  file into JSON rows (`services/query.py`, a fixed `read_parquet` over bytes
  fetched from the agent). It must never `ATTACH` storage, load extensions, or
  `.execute()` user SQL. All user-query execution happens on agents.
- **I2 — Agents initiate the control connection; the control plane does
  not.** The control plane holds no static agent inventory and never dials an
  agent's control channel. Its only outbound reach to an agent is the HTTP
  result read (the API fetches the result Parquet and decodes it to JSON).
- **I3 — Apache Polaris owns catalog metadata; Postgres owns DuckHaven
  entities.** Never persist catalog *structure* (schemas, tables, columns) into
  Postgres or treat DuckHaven's database as a catalog cache. Postgres may hold
  a supplementary `table_metadata` sidecar — ownership, last-write provenance,
  and row/size stats that Polaris does not track — keyed by the Polaris schema/table name.
- **I4 — One workspace, one storage backend, forever.** The binding is set
  at creation and is immutable. Every table's `storage_location` derives from
  its workspace backend's `root_uri`.
- **I5 — The control↔agent wire format lives only in `shared/`.** Both
  `api/` and `agent/` import `duckhaven_shared`. Never define a frame type or
  payload shape independently on one side.
- **I6 — Dependency direction is one-way:** `api → shared` and
  `agent → shared`. `shared` depends on neither; `api` and `agent` never
  import each other.
- **I7 — Storage credentials are short-lived and connection-scoped.** Creds
  are vended per `(agent, workspace)`, applied as a DuckDB `SECRET` on the
  per-query connection, and never written to disk on the agent.
- **I8 — Data + catalog DDL reach an agent; sandbox escapes do not.**
  `sql_guard` allows `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`MERGE` and
  `CREATE`/`ALTER`/`DROP`, executed on the agent against the attached Polaris
  REST catalog, and rejects anything that could break out of the per-query
  sandbox (`ATTACH`/`DETACH`, `COPY`/`EXPORT`, `INSTALL`/`LOAD`, `SET`/`PRAGMA`,
  `CALL`, `VACUUM`, transaction control). Only a single `SELECT` is materialized
  to Parquet; other statements run directly and return no result grid. Structured
  catalog DDL (create/drop schema, create/drop table) is **also** exposed as REST
  endpoints driving the catalog UI; ALTER from the UI is generated as SQL and run
  through the query path.
- **I9 — Postgres is the only state-of-record.** No second source of truth
  (no Redis, no in-memory queue surviving a restart). The in-memory agent
  registry is an ephemeral index of live sockets, not state.
- **I10 — Authorization happens at the API boundary** via
  `assert_workspace_member` before any dispatch. Polaris grants are
  defense-in-depth, not the primary gate.

---

## 12. Common Change Scenarios

A quick "if you want to do X, start here" index for contributors and agents.

| You want to… | Start in | Also touch |
|---|---|---|
| Add a control↔agent message | `shared/.../protocol.py` (new `FrameType`) | sender + handler in `api/.../routers/agents_ws.py` / `services/query.py` and `agent/.../control/channel.py` |
| Add a REST endpoint | `api/.../routers/<resource>.py` | a `schemas/` DTO, register in `main.py`, a test under `api/tests/unit/routers/`, and the web `src/api/` + `src/queries/` |
| Add/alter a Postgres table | `api/.../models/` | a new migration in `api/alembic/versions/` (`make migrate-new name=...`) |
| Change query execution (extensions, pragmas, attach) | `agent/.../executor/runner.py` | `supervisor.py` if it affects timeout/cancel |
| Widen/narrow the SQL allowlist | `api/.../services/sql_guard.py` | its test in `tests/unit/services/test_sql_guard.py`; the runner's single-`SELECT` branch in `agent/.../executor/runner.py` |
| Add a catalog DDL UI action | `api/.../routers/schemas.py` (REST endpoint) + `services/polaris.py` | web `src/features/catalog/CatalogNodeMenu.tsx` (+ dialogs), `src/api/schemas.ts`, `src/queries/schemas.mutations.ts`, MSW handler, tests |
| Add a storage backend kind | `api/.../services/agent_capabilities.py` (required extension) + `services/workspace.py` (`polaris_storage`) | `agent/.../executor/runner.py` (iceberg attach), `StorageBackend` validation, web `StorageIcon`/wizard |
| Change catalog credentials | `agent/.../config.py` + `api/.../config.py` (Polaris client id/secret) | `agent/.../executor/runner.py` (iceberg `SECRET`) |
| Add a UI screen | `web/src/features/<feature>/` | `src/router.tsx`, `src/api/` + `src/queries/`, MSW handler in `src/mock/handlers/`, a test under `web/tests/` |
| Change the agent handshake/auth | `api/.../routers/agents_ws.py` + `api/.../routers/admin/agents.py` | `agent/.../control/channel.py`, `agent/.../auth.py` |

Per project convention, **every change ships with tests** (pytest for
`api`/`agent`, Vitest + RTL + MSW for `web`) and passes
`make test && pre-commit run --all-files`.

---

## 13. Known Technical Debt

Honest, stable-enough caveats. The live, itemized list lives in the issue
tracker and the [README roadmap](../README.md#roadmap); this section names
the *categories* a contributor should be aware of.

- **External cloud-backend storage config.** The `s3`/`adls_gen2` kinds
  (operator-owned external object stores) still need their Polaris
  `storageConfigInfo` credential wiring (role ARN / tenant) completed in
  `services/workspace.polaris_storage`. Their write paths are validated behind
  opt-in/env-gated integration tests. The bundled-MinIO `object_store` path
  is fully wired (see §7).
- **DuckDB-iceberg DDL coverage.** `DROP` now purges (catalogs enable
  drop-with-purge and grant full ownership), but the breadth of `CREATE`/`ALTER`
  support against the Polaris REST catalog is bounded by the DuckDB `iceberg`
  extension version on the agent. The UI Alter flow generates SQL run through the
  agent, so any unsupported op surfaces as a query error rather than a silent
  no-op.
- **Single control-plane box.** The control plane is intentionally
  single-node (Postgres + Polaris + API on one host). High availability is a
  future concern, not a current property.
- **Result durability.** Results live only on the executing agent until the
  retention sweep removes them; they are not replicated. The recovery story
  is "re-run from saved SQL," not "fetch the old result."
- **Progress reporting is coarse.** `query_progress` frames are persisted to
  `queries.progress`, but the UI surfaces a binary running/done rather than
  streaming progress.

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Control plane** | The `duckhaven-api` process (with Postgres + Polaris). Orchestrates; never runs DuckDB queries. |
| **Agent** | A `duckhaven-agent` process embedding DuckDB, running on its own host, dialing home over WebSocket. The unit of compute. |
| **Workspace** | A governance + collaboration boundary. Maps 1:1 to an Apache Polaris catalog and is pinned to exactly one storage backend. |
| **Storage backend** | A physical location for Iceberg tables (Object storage, S3, ADLS Gen 2), registered once and referenced by workspaces. |
| **Catalog-managed table** | An Iceberg table whose commits are arbitrated by Apache Polaris (every Polaris REST table is catalog-managed). |
| **Bootstrap token** | A single-use credential an operator generates so a new agent can register. Exchanged once for a long-lived agent session token. |
| **Capabilities** | The document an agent advertises (DuckDB version, loaded extensions, memory ceiling) used to match agents to workspace backends. |
| **Frame** | One JSON message on the control WebSocket: `{type, payload}`, defined in `duckhaven-shared`. |
| **Vended credentials** | Short-lived storage credentials minted by Apache Polaris per `(agent, workspace)` and applied as a connection-scoped DuckDB `SECRET`. |

---

*This document describes the structure and invariants of DuckHaven. When the
structure changes, update this map; when only progress changes, update the
tracker instead.*
