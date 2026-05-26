# duckhaven — Architecture

> **Context for this revision.** RFC v0.2 was the pre-implementation design.
> M0 (walking spike), M1 (frontend on MSW), M2 (backend + agent + unit
> tests) and M3 (UC catalog + writes) have since landed. This revision
> (v0.4) keeps the design intent intact but updates every decision, the
> §6 API sketch, §5 storage layout, §11 milestones and §13 spike status
> to match what was actually built, and adds §8.a (shared protocol
> package) plus §16 (implementation status & known gaps) to make the
> delta explicit.
>
> **Status:** RFC v0.4 — implementation in progress. M0–M3 complete on
> merge; M4 (multi-agent + hardening) pending. Subsequent revisions are
> expected as M4 closes the remaining gaps listed in §16.
>
> **Status badges** appear next to every decision:
> `✓ Implemented` · `◐ Partial` · `○ Pending`.
>
> **Audience:** the engineer(s) implementing duckhaven.

---

## 1. What duckhaven Is

A self-hosted, browser-based SQL workspace and **federation control plane**
over Delta Lake tables governed by Unity Catalog. duckhaven's control plane
runs on a single homelab-class box on a Tailscale network; the compute and
storage it dispatches against are explicitly distributed.

| | |
|---|---|
| Control-plane unit | One `docker compose` stack on one Linux box (`deploy/docker-compose.yml`) |
| Control-plane floor | 8 GB RAM, 50 GB SSD, 2–4 cores (no DuckDB locally) |
| Compute | 1..N DuckDB **agents** registered with the control plane |
| Agent host floor | 8 GB RAM per agent (DuckDB query memory dominates) |
| Network | Tailscale-only; agents dial control plane outbound |
| Users | 2–10, local accounts, per-user/per-group workspaces + shared "public" |
| Engines | DuckDB only (multi-node, multi-version) |
| Storage backends (MVP) | Local FS, NAS (mounted FS), S3, ADLS Gen 2 |
| Catalog | Unity Catalog OSS (also vends storage credentials) |
| Frontend | React SPA (SQL worksheets, no notebooks) |
| Backend | FastAPI + Postgres |
| Workspace layout | `uv` monorepo: `api/`, `agent/`, `shared/` (Python) + `web/` (React) + `deploy/`, `scripts/` |

Two facts shape every decision below: (a) **the user picks the engine per
worksheet** — duckhaven is dispatcher, not optimizer; (b) **each workspace is
bound to exactly one storage backend** — duckhaven enforces this at
workspace-create time and at every write.

---

## 2. Non-Goals (explicit)

*Unchanged from v0.2.*

- Not a Spark / Databricks replacement. No distributed query plan; engines
  are independent DuckDB processes.
- Not multi-engine in MVP. No Spark, Trino, Polars. The agent contract is
  drawn so a second engine type can be added later without re-architecting.
- Not a notebook platform. SQL worksheets only.
- Not internet-exposed. Tailscale is the network perimeter.
- Not authoritative storage. Source data lives elsewhere.
- Not an ingestion engine. External writers (delta-rs, dlt, Airbyte) feed it.
- No row-level / column-level security in MVP. Workspace-level only.
- No `UPDATE / MERGE / DELETE` in MVP (DuckDB UC extension does not yet
  support them).
- No cross-workspace joins in MVP.
- No agent autoscaling, no agent-level cost-based routing. Users pick.

---

## 3. System Diagram

```
   Browser ───────────────────────┐
   (Tailscale)                    │  HTTP  (direct → api:8000)
                ┌─────────────────▼────────────────────────────┐
                │  duckhaven-api  (FastAPI)  — control plane   │
                │  • auth, workspaces, queries, audit          │
                │  • agent registry + dispatcher (WS)          │
                │  • storage-backend registry                  │
                │  • UC client + DDL                           │
                └─┬───────────────┬─────────────────┬──────────┘
                  │               │                 │
        ┌─────────▼─────┐ ┌───────▼────────┐ ┌──────▼─────────┐
        │ Postgres 16   │ │ unity-catalog  │ │ Agent control  │
        │ users / ws /  │ │  REST :8080    │ │ channel (WS)   │
        │ queries / etc │ │  catalog +     │ │ — agents dial  │
        │  + UC store   │ │  cred vendor   │ │   home         │
        └───────────────┘ └────────┬───────┘ └──────┬─────────┘
                                   │                │
                                   │     ┌──────────┴────────────────┐
                                   │     ▼                            ▼
                          ┌────────┴────────┐             ┌────────────────────┐
                          │ duckhaven-agent │             │ duckhaven-agent    │
                          │  (VM / host A)  │   ...       │  (VM / host N)     │
                          │ • DuckDB ≥1.5   │             │ • DuckDB ≥1.5      │
                          │ • shared proto  │             │ • shared proto     │
                          │ • per-query cap │             │ • per-query cap    │
                          │ • result HTTP   │             │ • result HTTP      │
                          └──────┬──────────┘             └──────┬─────────────┘
                                 │ short-lived creds             │
                       ┌─────────▼────────┐ ┌──────────────┐ ┌───▼────────────┐
                       │ Local FS / NAS   │ │  S3 bucket   │ │ ADLS Gen 2     │
                       └──────────────────┘ └──────────────┘ └────────────────┘
                          (workspace-pinned: each workspace → exactly one backend)
```

Implementation note: in the current `deploy/docker-compose.yml`, the control
plane services are `postgres`, `unity-catalog`, and `api`. The `api` service
publishes port `8000` directly on the host (private Tailscale network — no
edge TLS terminator). Agents are deployed separately (per D12) and are **not**
part of the compose stack.

---

## 4. Architectural Decisions

Each decision is presented in **Decision / Context / Consequences** form,
prefixed by an implementation-status badge and (when reality has diverged)
a `Current state` note.

---

### D1 — Federated DuckDB agents; control plane is dispatcher only · `✓ Implemented`

**Decision.** Compute lives in **`duckhaven-agent`** processes, each
embedding one DuckDB engine, running on a separate host/VM from the control
plane. The control plane (`duckhaven-api`) **does not run DuckDB**; it
dispatches queries to a chosen agent over a job-dispatch RPC. The user
selects the agent per worksheet (see D15).

**Context.** Decoupling control from compute makes hardware decisions
elastic, supports heterogeneous DuckDB versions side-by-side, and removes
the single-process OOM blast radius.

**Current state.** Dispatch path implemented end-to-end:
`api/src/api/routers/queries.py` → `api/src/api/routers/agents_ws.py`
(WebSocket fan-out via `ConnectionManager` in
`api/src/api/services/agent_registry.py`) → `agent/src/agent/control/channel.py`
→ `agent/src/agent/executor/runner.py`. Validated by
`api/tests/unit/test_queries.py` and `agent/tests/unit/control/test_channel.py`.

**Consequences.**
- Control-plane footprint stays small (no DuckDB there).
- The wire between control plane and agent is a small JSON frame protocol
  over WebSocket (D16), defined in `duckhaven-shared` (§8.a).
- Cross-agent atomicity does not exist; a `BEGIN/COMMIT` lives in one agent.
- Agent crash kills its in-flight queries; clients see them as `failed`.

---

### D2 — Per-query memory cap + wall-clock timeout, enforced at the agent · `◐ Partial`

**Decision.** Every query, before execution, sets `memory_limit` (default
`6GB`) and `statement_timeout` (default `10min`). The **agent** owns the
supervisor and the DuckDB interrupt. Defaults are workspace-overridable;
operator-set ceilings on the agent are non-overridable.

**Current state.**
- `memory_limit` PRAGMA is set per query in `agent/src/agent/executor/runner.py`
  before execution.
- Wall-clock timeout is enforced by `asyncio.wait_for` in
  `agent/src/agent/executor/supervisor.py`.
- **Gap (G-D2-a):** DuckDB's query interrupt API is **not** wired. A
  long-running query that doesn't yield will block its executor thread until
  process restart. Tracked in §16.
- **Gap (G-D2-b):** No per-host operator ceiling above which workspace
  overrides cannot rise; ceilings are config-only.

**Consequences.**
- Memory-capped queries fail cleanly today.
- Time-capped queries return `asyncio.TimeoutError` cleanly, but the
  underlying DuckDB thread may continue until natural completion — close
  G-D2-a before stress testing in M4 (see S4 in §13).

---

### D3 — Local user table + bcrypt + sessions; no external IdP · `✓ Implemented`

**Decision.** Identity lives in `users(id, email, password_hash[bcrypt-12+],
…)`. Sessions are HTTP-only `Secure` cookies, SameSite=Lax. A
`credentials(id, user_id, agent_id, kind[session|agent_bootstrap|agent_session],
token, expires_at, …)` table covers user sessions and the agent
bootstrap/session credentials (D14).

**Current state.** Implemented in `api/src/api/services/auth.py` (bcrypt
rounds=12) + `api/src/api/routers/auth.py` (`POST /auth/login`,
`POST /auth/logout`, `GET /me`). Session TTL is 7 days (configurable). Admin
seeding via `scripts/seed-admin.py`. Tests in `api/tests/unit/test_auth.py`.

**Consequences.** Unchanged from v0.2.

---

### D4 — SQL worksheets only; no notebooks in MVP · `✓ Implemented`

**Decision.** The frontend offers a Monaco-based SQL editor + result grid +
saved queries + table browser + **engine selector**. No cell model.

**Current state.** Implemented in `web/src/features/worksheet/WorksheetPage.tsx`
(tabs, dirty indicator, Monaco editor at `SqlEditor.tsx`, `CatalogTree.tsx`,
`ResultsTable.tsx`). Engine selector lives in
`web/src/components/app/AgentPicker.tsx` (see D15).

---

### D5 — Async query lifecycle; results materialized on the executing agent · `✓ Implemented`

**Decision.**
- `POST /workspaces/{ws}/queries` with `{sql, agent_id}` →
  `{id, status:'queued'}`.
- The control plane writes the query row to Postgres and pushes a
  `dispatch_query` frame to the chosen agent's WebSocket channel.
- The agent executes locally, materializes the result to
  `/var/duckhaven-agent/results/{query_uuid}.parquet` **on the agent**, and
  reports back `{status, row_count, duration_ms, result_path}`.
- `GET /queries/{id}` → metadata from Postgres.
- `GET /queries/{id}/rows` → control plane proxies a ranged HTTP read from
  the agent that holds the result (HTTP `Range` header, not query cursor).
- `DELETE /queries/{id}` → `cancel_query` frame forwarded to the agent.

**Current state.** Wired end-to-end in
`api/src/api/services/query.py`, `api/src/api/routers/queries.py`,
`agent/src/agent/results/server.py`. `queries` table in Postgres is the
single state-of-record (no Redis). Parquet retention is **agent uptime only**
today (gap G-D5-a: the 24h retention sweep is not yet implemented).

**Consequences.**
- Postgres remains the query-state-of-record.
- Result lifetime is bounded by agent uptime; affected queries are
  re-runnable from saved SQL.
- Pagination latency adds one control-plane hop on top of the agent's local
  Parquet read.

---

### D6 — Pluggable storage backends; per-workspace binding · `✓ Implemented`

**Decision.** A workspace is bound to **exactly one** storage backend at
create time. Supported backends in MVP:

| Backend kind | URI prefix | DuckDB extension | Mount story |
|---|---|---|---|
| `local_fs` | `file:///var/duckhaven-agent/data/...` | none | Path mounted on every agent that serves the workspace |
| `nas` | `file:///mnt/<name>/...` | none | NFS/SMB mounted on every agent that serves the workspace |
| `s3` | `s3://bucket/prefix/...` | `httpfs` | Network — no mount |
| `adls_gen2` | `abfss://container@account/...` | `azure` | Network — no mount |

The control plane holds a **`storage_backends`** registry: `(id, kind, name,
root_uri, uc_storage_credential_id, created_by, created_at)`. Workspaces
reference a backend by id.

**Current state.** Registry CRUD endpoints implemented under
`/admin/storage-backends` (`api/src/api/routers/admin/storage.py`). Models
in `api/src/api/models/storage_backend.py`, `workspace.py`. Web admin wizard
in `web/src/features/admin/StorageBackendsPage.tsx`. Workspaces immutable
once bound. Delete-guard prevents removing a backend that any workspace
references. Tests in `api/tests/unit/test_admin/storage.py`.

**Consequences.** Unchanged from v0.2.

---

### D7 — Unity Catalog OSS, used both as catalog and as credential vendor · `✓ Implemented`

**Decision.** Unity Catalog OSS runs as a JVM container. **One UC catalog
per duckhaven workspace.** UC additionally holds `storage_credentials` and
`external_locations` for every workspace backend. At query dispatch time,
the control plane requests **short-lived credentials** from UC scoped to the
workspace's backend and forwards them to the agent.

**Current state.** `api/src/api/services/unity_catalog.py` wraps the
`unitycatalog` Python SDK with raw `httpx` fall-through for endpoints the
SDK lacks (notably `temporary-table-credentials`). `services/uc_credentials.py`
caches vended creds per `(agent_id, workspace_id)` with half-TTL refresh,
sized by `cred_safety_window_s`. `services/query.py:dispatch_query` adds
`backend` + `storage_credentials` keys to the `DISPATCH_QUERY` payload for
S3/ADLS workspaces; local-fs/NAS workspaces send no creds and rely on the
agent's filesystem mounts. UC catalog is eager-created on
`POST /workspaces` (with rollback on UC failure) and lazy-self-healed for
pre-M3 workspaces on first `POST /schemas` or `POST /queries`.

**Consequences.** Unchanged from v0.2: short-lived creds, control plane
on the hot path, R3 (vending bottleneck) is now mitigated by the cache.

---

### D8 — duckhaven owns DDL via UC REST; DuckDB on agents executes DML · `✓ Implemented`

**Decision.** Table creation (`CREATE TABLE`) is performed by `duckhaven-api`
calling Unity Catalog's REST API directly with the workspace's backend as
the table's storage root. INSERT and SELECT run on a selected agent.
UPDATE/MERGE/DELETE are out of scope until DuckDB ships support.

**Current state.**
- `api/src/api/routers/schemas.py` exposes
  `GET/POST /workspaces/{ws}/schemas` and
  `GET/POST /workspaces/{ws}/schemas/{s}/tables`, gated on
  `assert_workspace_member` (`reader` for list, `writer` for create).
- SQL allowlist lives in `api/src/api/services/sql_guard.py`. It calls
  `duckdb.json_serialize_sql` on a module-level in-memory connection and
  walks the AST; only top-level `SELECT` and `INSERT` (incl.
  `INSERT … SELECT`) are accepted. The connection **parses only — no
  execution, no extensions, no storage** (narrow carve-out of D1, the
  only DuckDB code path on the control plane).
- Web wires Create-Schema / Create-Table via
  `web/src/features/catalog/CreateSchemaDialog.tsx` and
  `CreateTableDialog.tsx`. Rename / Drop remain placeholders (M4).

---

### D9 — Catalog Commits ON for every duckhaven-managed table · `✓ Implemented`

**Decision.** Every table created through duckhaven's Create-Table flow has
`TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')`. UC arbitrates
all writes; conflicting writers receive a conflict error and retry.

**Current state.** `POST /workspaces/{ws}/schemas/{s}/tables` in
`api/src/api/routers/schemas.py` always sets
`delta.feature.catalogManaged=supported` and `format=DELTA`, and always
points `storage_location` at the workspace backend's `root_uri +
/{schema}/{table}/`. End-to-end agent INSERT against UC-tracked
catalogManaged Delta tables is **not yet verified on UC OSS 0.4.0** —
spike S3 found that UC OSS 0.4.0 lacks the `/delta/preview/commits`
endpoint DuckDB's `unity_catalog` extension uses for catalogManaged
writes. The property is recorded in UC; runtime enforcement re-opens
once UC OSS ships coordinated-commits.

---

### D10 — Workspace-level permissions; permissions and storage co-pinned · `✓ Implemented`

**Decision.** Permissions are workspace-scoped: `workspace_members(workspace_id,
user_id, role[owner|writer|reader])`. duckhaven enforces this at the API
boundary; UC grants are mirrored as defense-in-depth.

**Current state.** Enforced by `assert_workspace_member()` in
`api/src/api/services/workspace.py`; role hierarchy reader < writer < owner.
Endpoints `GET /workspaces/{ws}/members`, `POST /workspaces/{ws}/members`
implemented. **UC mirror is not active**; duckhaven is currently the
sole permission authority. Mirror tracked as gap **G-D10-a** (M4). Tests
in `api/tests/unit/test_workspaces.py`.

---

### D11 — Audit log: who/when/SQL/agent/duration/rows/status · `✓ Implemented`

**Decision.** A query audit row records every query the system runs:
user, workspace, agent, SQL, status, timestamps, row count, duration, and
error (if any).

**Current state.** Audit is read directly from the `queries` table via
`GET /admin/audit?workspace=&agent=&user=&since=&until=`
(`api/src/api/routers/admin/audit.py`). Alembic migration
`0002_add_queries_user_id.py` added `queries.user_id` (nullable FK to
`users.id`, indexed); `services/query.py:dispatch_query` populates it
from `current_user`. The web UI at `web/src/features/admin/AuditPage.tsx`
exposes a user dropdown populated from `GET /admin/users`. No separate
`audit_queries` table — the live `queries` row is the audit row.

---

### D12 — Deployment: control plane as `docker compose`; agents as separate processes · `◐ Partial`

**Decision.** Control-plane services live in one `docker-compose.yml` with
pinned image tags. Agents are deployed separately (one container image per
host).

**Current state.**
- `deploy/docker-compose.yml` runs `postgres:16-alpine`,
  `unitycatalog/unitycatalog:0.4.0`, `duckhaven-api` (publishing `:8000`).
- `agent/Dockerfile` exists and **is built + pushed** to
  `ghcr.io/<owner>/duckhaven-agent` (and `duckhaven-api`) by
  `.github/workflows/build.yml` on every `v*.*.*` tag. G-D12-a closed.
- `duckhaven-api` is no longer `:latest`: `deploy/docker-compose.yml` pins
  it via `${DUCKHAVEN_API_IMAGE:-duckhaven-api:0.1.0}`, overridable to the
  GHCR image. G-D12-b closed.
- Postgres + Alembic migrations run via `make migrate`
  (`uv run --package duckhaven-api alembic upgrade head`); they are not yet
  wired into container start.
- Tailscale-only ingress per design; the `api` service is exposed directly
  on `:8000` (no edge reverse proxy or TLS terminator).

---

### D13 — UI-first frontend; mocked backend during M1 · `✓ Implemented`

**Decision.** M0 walking spike validates dispatch end-to-end. The frontend
is built against a mock backend implementing §6. The real backend (M2) is
implemented to satisfy that contract.

**Current state.** M1 complete (PR #18). MSW handlers live under
`web/src/mock/handlers/*` and are loaded only in `import.meta.env.DEV` from
`web/src/main.tsx`. In production builds the SPA hits `/api/*` directly
(Vite dev-server proxy maps `/api` → `http://localhost:8000`).
`onUnhandledRequest: 'bypass'` in dev so any newly wired endpoint reaches
the real API automatically.

---

### D14 — Agents dial home with a one-time bootstrap token; control plane never initiates connections · `✓ Implemented`

**Decision.** Operator generates a bootstrap token in the duckhaven admin
UI (or via `scripts/gen-token.sh`). Token is single-use, scoped to a
label-free agent identity, valid for 24h. The agent starts with the token +
the control-plane URL, opens an outbound WebSocket to `/agents/connect`,
exchanges the bootstrap token for a long-lived **agent credential**
(`credentials.kind = 'agent_session'`), and holds the WebSocket open for the
rest of its life.

**Current state.** Implemented in
`api/src/api/routers/admin/agents.py` (POST `/admin/agents/bootstrap`,
DELETE `/admin/agents/{id}/credential`) and the WebSocket handler in
`api/src/api/routers/agents_ws.py`. Agent side:
`agent/src/agent/control/channel.py` (AUTH → AUTH_OK → AGENT_STATUS, 5 s
reconnect backoff). Tests:
`api/tests/unit/test_admin/agents.py`, `agent/tests/unit/control/test_channel.py`.

**Open gap (G-D14-a):** the long-lived agent credential is also the token
the agent's HTTP result endpoint expects as a Bearer header (see D16), but
the agent's `main.py` currently hardcodes the bearer to an empty string —
result reads succeed today only because the control-plane proxy doesn't
send a real bearer either. Tighten this together with G-D16-a.

---

### D15 — User picks the executing agent per worksheet/query · `✓ Implemented`

**Decision.** Every worksheet has an **engine selector** populated from the
agents the user's workspaces can address. Each query carries an `agent_id`.

**Current state.** Implemented in `web/src/components/app/AgentPicker.tsx`,
embedded in the worksheet toolbar. The picker also performs the
backend-compatibility filter described in D17 (currently **client-side**;
see D17 `Current state`). Saved queries record `default_agent_id`
(`api/src/api/models/saved_query.py`).

---

### D16 — Agent control protocol: WebSocket dispatch, HTTP result range reads · `✓ Implemented`

**Decision.** Two channels between control plane and agent:

1. **Control channel** — one long-lived WebSocket per agent (initiated by
   the agent). Frames: `auth`, `auth_ok`, `dispatch_query`, `query_progress`,
   `query_done`, `cancel_query`, `heartbeat`, `agent_status`. JSON; small
   payloads only. The frame schema lives in `duckhaven-shared` (§8.a).
2. **Result-read channel** — control plane issues `GET` requests to the
   agent's local HTTP endpoint (signed with the agent credential, Bearer
   header) for **HTTP-Range** reads of `results/{uuid}.parquet`. The agent
   listens on `127.0.0.1:8001` by default; binding to the Tailscale address
   is an operator choice via `.env`.

**Current state.** Implemented; happy path validated by
`api/tests/unit/test_queries.py` (rows-proxy and cancel) and
`agent/tests/unit/results/test_server.py` (200/401/404 paths).

**Open gaps.**
- **G-D16-a:** Agent result-server bearer is hardcoded empty
  (`agent/src/agent/main.py`); must be populated from the `auth_ok` payload's
  session token. Tied to G-D14-a.
- **G-D16-b:** `query_progress` frames are recognised in
  `api/src/api/routers/agents_ws.py` but not persisted; `GET /queries/{id}`
  does not expose progress. Acceptable for MVP, revisit in M4.

---

### D17 — Agent capability advertisement; dispatch-time backend compatibility check · `◐ Partial`

**Decision.** On connect (and at every heartbeat), an agent advertises a
small **capabilities document**: DuckDB version, list of loaded extensions
(`unity_catalog`, `delta`, `httpfs`, `azure`, …), per-query memory ceiling,
and host info. The control plane stores the latest doc per agent. At query
dispatch time the control plane checks that the agent's extensions cover
the workspace's backend kind; mismatches reject the query with a
remediation hint.

**Current state.**
- Schema: `duckhaven_shared.schemas.AgentCapabilities` (DuckDB version,
  extensions, memory limit, cores, optional `tailscale_ip` and `host`).
- Advertisement: the agent sends `AGENT_STATUS` on initial connect
  (`agent/src/agent/control/channel.py:_get_capabilities`). **Heartbeats do
  NOT re-send capabilities today** (gap G-D17-a).
- `agent/Dockerfile` pre-installs `httpfs`, `azure`, `unity_catalog`,
  `delta` at image build time so `_get_capabilities` reports them on the
  first connect (no first-query latency).
- Storage: control plane persists the JSON document in `agents.capabilities`
  on `AGENT_STATUS` receipt.
- **Compatibility check at dispatch is currently client-side**: the React
  `AgentPicker` calls `agentSupportsBackend()` and disables/warns on
  incompatible agents, but `api/src/api/services/query.py` does not enforce
  the check at dispatch time (gap G-D17-b). Any non-web client could
  dispatch a workspace to an agent without the right extension and get a
  DuckDB error at runtime.

---

### D18 — DR: nightly `pg_dump`; data DR is delegated to backend · `◐ Partial`

**Decision.** Control-plane Postgres (duckhaven app state + UC metastore)
is `pg_dump`'d nightly to a second disk. Data DR depends on backend kind.

**Current state.**
- `scripts/pg-backup.sh` exists, dumps to
  `/var/duckhaven/backups/duckhaven_<ts>.sql.gz` via
  `docker compose exec postgres pg_dump`. **Not wired to cron or systemd;
  operator must schedule it** (gap G-D18-a). The backup directory is on the
  same disk by default, against the spirit of D18 — runbook needs to
  document "point this at a second disk / NAS mount" (gap G-D18-b).
- Conditional DR banner described in v0.2 is **not yet in the web UI**
  (gap G-D18-c) — `web/src/features/admin/StorageBackendsPage.tsx` shows
  the backend kind but no DR badge yet.

---

## 5. Storage Layout (canonical)

### Control plane (`deploy/docker-compose.yml` named volumes)

| Path / Volume | Owner | Purpose |
|---|---|---|
| `postgres_data` | postgres | duckhaven app state + UC metastore |
| `uc_data` | unity-catalog | UC's other state |
| `api_data` | api | `/var/duckhaven` inside the api container (reserved) |

`pg-backup.sh` writes to `/var/duckhaven/backups/duckhaven_<ts>.sql.gz` on
the host (default — operator should redirect to a second disk).

### Each agent host

```
/var/duckhaven-agent/
  results/{query_uuid}.parquet         # D5 materialized results (24h retention — G-D5-a)
  cache/                               # optional DuckDB http/object-store cache
  agent.toml / .env                    # control-plane URL + agent credential
  mounts/                              # NAS/FS mounts (operator-configured)
```

### Backend roots (workspace-pinned; example layouts)

```
file:///var/duckhaven-agent/data/{workspace}/{schema}/{table}/_delta_log/
file:///mnt/nas01/{workspace}/{schema}/{table}/_delta_log/
s3://my-bucket/duckhaven/{workspace}/{schema}/{table}/_delta_log/
abfss://container@acct/duckhaven/{workspace}/{schema}/{table}/_delta_log/
```

---

## 6. API Contract (current implementation)

The shapes below describe what `api/src/api/routers/*` actually serves
today. Endpoints from the v0.2 sketch that are not yet implemented are
listed explicitly under `Pending` so the gap is visible.

```
# Auth & users — D3
POST   /auth/login                   {email, password}             → sets session cookie
POST   /auth/logout
GET    /me                                                         → user

# Workspaces — D6, D10
GET    /workspaces                                                 → [workspace]
POST   /workspaces                   {slug, name, storage_backend_id}  → workspace
GET    /workspaces/{ws}                                            → workspace
GET    /workspaces/{ws}/members
POST   /workspaces/{ws}/members      {user_id, role}

# Storage backends — D6 (admin)
GET    /admin/storage-backends                                     → [backend]
POST   /admin/storage-backends       {kind, name, root_uri, uc_storage_credential_id?}
DELETE /admin/storage-backends/{id}                                → 409 if any workspace uses it

# Agents — D14, D15, D17
GET    /agents                                                     → [agent + capabilities] (for engine picker)
GET    /admin/agents                                               → admin variant (includes inactive)
POST   /admin/agents/bootstrap                                     → {token, expires_at}   (D14)
DELETE /admin/agents/{agent_id}/credential                         → revoke session
WS     /agents/connect                                             # agent-initiated, bootstrap or session token

# Queries — D5
POST   /workspaces/{ws}/queries      {sql, agent_id, [memory_limit_gb, timeout_s]}
                                                                   → {id, status:'queued'}
GET    /queries/{id}                                               → metadata (incl. agent_id)
GET    /queries/{id}/rows            # HTTP Range header — proxied range read from agent
DELETE /queries/{id}                                               → cancel signal to agent

# Saved queries
GET    /workspaces/{ws}/saved-queries
POST   /workspaces/{ws}/saved-queries   {name, sql, default_agent_id}

# Schemas / tables — D8, D9
GET    /workspaces/{ws}/schemas
POST   /workspaces/{ws}/schemas                {name}
GET    /workspaces/{ws}/schemas/{s}/tables
POST   /workspaces/{ws}/schemas/{s}/tables     {name, columns:[{name,type,nullable}]}
GET    /workspaces/{ws}/schemas/{s}/tables/{t}

# Audit — D11 (read-only)
GET    /admin/audit?workspace=&agent=&user=&since=&until=          → audit rows from queries table
```

**Pending (M4+):**

```
# Schemas / tables — D8 (extensions)
DELETE /workspaces/{ws}/schemas/{s}/tables/{t}
```

Pagination on `GET /queries/{id}/rows` is **HTTP `Range` based**, not query
cursor; the v0.2 sketch's `?cursor&limit` was rejected during M2 in favour
of Range so the proxy can stay zero-copy.

---

## 7. Resource Budget

*Unchanged from v0.2.*

### Control-plane box (8 GB floor)

| Component | Resident | Notes |
|---|---|---|
| Linux + page cache | ~1.0 GB | |
| Postgres 16 | ~600 MB | duckhaven app + UC metastore |
| Unity Catalog OSS (JVM) | ~2.0 GB | Dictates the 8 GB floor |
| `duckhaven-api` (FastAPI) | ~500 MB | Includes WebSocket fan-out |
| **Baseline committed** | **~4.2 GB** | |
| **Headroom for traffic + page cache** | **~3.8 GB** | |

### Per agent host (8 GB floor)

| Component | Resident | Notes |
|---|---|---|
| Linux + page cache | ~1.0 GB | |
| `duckhaven-agent` idle | ~400 MB | DuckDB embedded |
| Per-query budget | up to 6 GB | `memory_limit` PRAGMA (D2) |
| **Baseline committed** | **~1.4 GB** | |
| **Headroom** | **~6.6 GB** | Sized for one full-cap query at a time |

---

## 8. Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React 19 + TypeScript + Vite 8, Monaco editor, TanStack Router + Query + Table, Radix UI + shadcn/ui + Tailwind |
| API | FastAPI (Python 3.14), async; `websockets` for agent channel |
| ORM / migrations | SQLAlchemy 2.x (async) + Alembic |
| Job dispatch | Direct WebSocket push to chosen agent; Postgres holds query state of record (no Redis) |
| Agent runtime | Python 3.14 process embedding DuckDB; small HTTP server for result-range reads |
| Engine | DuckDB ≥ 1.5.x (pinned minor) — present **only** on agents |
| Catalog | Unity Catalog OSS 0.4.0 (pinned), used as catalog + credential vendor |
| Catalog client | `unitycatalog` Python SDK + raw `httpx` for endpoints the SDK lacks (`api/src/api/services/unity_catalog.py`) |
| Storage format | Delta Lake, Catalog Commits ON (D9), per-workspace backend (D6) |
| Storage drivers (in agent) | DuckDB native (FS), `httpfs` (S3), `azure` (ADLS Gen 2) — pre-installed by `agent/Dockerfile` |
| Ingress | `api` published directly on `:8000` over the private network (no edge TLS terminator) |
| Identity | Local users + bcrypt + cookies (D3); agent sessions via WS (D14) |
| Networking | Tailscale |
| Container | docker compose v2 (control plane); single container for agents (image build operator-driven) |
| Lint / format | Ruff (no mypy / no other typechecker) |
| Tests | pytest + pytest-asyncio (API + agent); Vitest + RTL + MSW (web). Targets: api ≥80% cov, agent ≥75% cov |
| Observability | JSON logs to stdout from every component + built-in audit UI |

### 8.a — Shared protocol package (`duckhaven-shared`)

`shared/` is a third `uv` workspace member (alongside `api/` and `agent/`)
that is the single source of truth for the control plane ↔ agent contract.
Both `api/pyproject.toml` and `agent/pyproject.toml` declare
`duckhaven-shared = { workspace = true }`. Dependency footprint is just
`pydantic>=2`.

Two modules:

- `duckhaven_shared.protocol` — defines `FrameType` (string enum of `auth`,
  `auth_ok`, `dispatch_query`, `query_progress`, `query_done`,
  `cancel_query`, `heartbeat`, `agent_status`) and `Frame`
  (`{type: FrameType, payload: dict[str, Any]}`). This is the wire format
  for D16's control channel.
- `duckhaven_shared.schemas` — defines `AgentCapabilities`
  (`duckdb_version`, `extensions: list[str]`, `memory_limit_gb`, `cores`,
  optional `tailscale_ip`, `host`). This is the D17 advertisement payload.

Consumers today:
- `api/src/api/services/query.py` — builds `Frame(FrameType.DISPATCH_QUERY, …)`
- `api/src/api/routers/agents_ws.py` — parses incoming frames and routes by
  `FrameType`
- `agent/src/agent/control/channel.py` — both sides of every frame; builds
  `AgentCapabilities` on connect

Adding a new frame type means changing this package and bumping both
api/ and agent/ to pick up the new enum — by design, the contract cannot
drift silently between sides.

---

## 9. Security Model

*Unchanged from v0.2 in intent. One concrete deviation:* G-D16-a means the
agent's result HTTP server currently accepts the bearer header but the
control-plane proxy and the agent itself both treat the token as empty.
Closing G-D14-a / G-D16-a restores the design.

- **Network perimeter:** Tailscale. No public ingress. The `api` service is
  exposed directly on `:8000` over the private network (plain HTTP); transport
  encryption is provided by the Tailscale/WireGuard tunnel.
- **Authentication (users):** D3.
- **Authentication (agents):** D14 — bootstrap token → long-lived agent
  credential; WebSocket bound to that credential; HTTP result endpoint
  signed with the same credential *(pending G-D14-a/G-D16-a)*.
- **Authorization:** D10. Every query is validated for `workspace_members`
  before dispatch.
- **Backend credentials:** D7 — UC vends short-lived per-(agent, workspace)
  creds, embedded into the `DISPATCH_QUERY` frame and applied by
  `CREATE SECRET` on the agent's per-query DuckDB connection.
- **Sandboxing (agents):** agent process has read+write only on
  `/var/duckhaven-agent/` and the backend roots its workspaces require.
- **Rate limiting:** login endpoint only, in-memory per-IP, 5 failed/min.
- **Secrets:** UC token, Postgres password, agent bootstrap secret held in
  `.env` outside the repo, consumed by docker compose. No vault.
- **In threat model:** misclick / accidental destructive query, cookie
  theft, leaked agent credential (short TTL on backend creds limits damage).
- **Out of threat model:** RCE in DuckDB extensions, internet-borne
  attackers, malicious duckhaven users or operators.

---

## 10. MVP Scope (definitive)

*Unchanged from v0.2 — the scope statement is still the target.* See §16
for current vs target coverage.

---

## 11. Milestones

| M | Goal | Status | Notes |
|---|---|---|---|
| **M0 — Walking spike** | One hardcoded SELECT through control plane + one agent. | ✓ Complete | Validated end-to-end during M2 build-out; D1/D14/D16 covered by integration of `agent/control/channel.py` + `api/routers/agents_ws.py`. |
| **M1 — UX on mocked backend** | High-fidelity React UI against §6 mock, including engine selector and backend registry. | ✓ Complete (PR #18 — `b7485fa`) | All screens shipped; MSW handlers under `web/src/mock/handlers/*`. |
| **M2 — Backend wiring** | Real FastAPI + Postgres + agent control protocol satisfy the M1 contract. | ✓ Complete (PR #19 — `5af1ce2`) | Auth (D3), workspaces (D6/D10), queries (D5), agents (D14–D17 partial), saved queries, audit (D11 partial), storage backends (D6). |
| **M3 — Catalog + writes** | Create Table via UC REST against any of the four backend kinds; INSERT from worksheet. | ✓ Complete (branch `feat/m3-catalog-writes`) | Closes G-D7-*, G-D8-*, G-D9-*, G-D11-*, G-D17-c. Schemas/tables endpoints + SQL allowlist + UC credential vending + audit `user_id` all landed. |
| **M4 — Multi-agent + hardening** | Two registered agents; engine selector exercised under load; production-ready single-control-plane box. | ○ Pending | Closes G-D2-a (DuckDB interrupt), G-D5-a (retention sweep), G-D10-a (UC permission mirror), G-D12-b (pin api image), G-D17-a/b (heartbeat caps + server-side compat check), G-D18-* (cron + banner). |

**Total estimated wall time remaining: ~2 weeks** (M4 only).

---

## 12. Risks

*v0.2's risks remain accurate; current standings updated below.*

| ID | Risk | L | I | Mitigation | Standing |
|---|---|---|---|---|---|
| R1 | Agent control protocol has rough edges at scale | M | M | Validated by `test_channel.py`; small JSON protocol | Lowered after M2 — cancel + reconnect paths green |
| R2 | Dial-home breaks behind aggressive NAT / firewalls | L | M | Tailscale removes most NAT; runbook covers MTU | Unchanged |
| R3 | UC credential vending becomes the bottleneck on hot dispatch | M | M | Cache vended creds per (agent, workspace) for ~½ TTL | Implemented in `services/uc_credentials.py` (half-TTL refresh) |
| R4 | duckhaven ↔ UC grant drift | M | M | v1.1 reconciliation cron | Not yet exercised — gated by G-D10-a (M4) |
| R5 | Agent crash mid-query loses materialized results | M | L | Re-runnable from saved SQL; D5 marks queries failed cleanly | Unchanged |
| R6 | DuckDB UC extension write path has rough edges on cloud backends | M | H | Spikes S3/S5 cover write paths | Confirmed rough: UC OSS 0.4.0 missing `/delta/preview/commits`; S3 local-fs spike currently skipped, cloud variants env-gated |
| R7 | INSERT against a non-CC table corrupts data | M | H | duckhaven refuses INSERT against non-CC tables (D9) | Enforced — every duckhaven-created table sets `delta.feature.catalogManaged=supported`; SQL allowlist rejects DDL outside `POST /tables` |
| R8 | Operator misconfigures a workspace backend | M | M | UC `external_locations` validated at workspace-create | Eager UC catalog create on `POST /workspaces` rolls back the pg row on UC failure |
| R9 | Tailscale outage = total platform outage | L | H | Document static-IP fallback in runbook | Unchanged |
| R10 | SSD failure on FS/NAS-backed workspace → data loss | L | C | Conditional UI banner (D18); v1.x `restic` | Banner G-D18-c |
| R11 | Catalog metadata loss | L | H | Nightly `pg_dump` (D18) | Script exists, cron pending (G-D18-a) |
| R12 | Pure UI-first sequencing forces backend rework | H | M | M0 spike grounds the contract | **Resolved** — M2 satisfied the §6 contract M1 was built against |

---

## 13. Validation Spikes (status post-M2)

| Spike | Topic | Status |
|---|---|---|
| **S1** | Agent control protocol (WS + range reads, cancel, disconnect→requeue) | ✓ Passed — covered by `agent/tests/unit/control/test_channel.py` + `api/tests/unit/test_queries.py` |
| **S2** | UC OSS + DuckDB attach (local FS) | ✓ Passed — `api/tests/integration/test_uc_smoke.py` |
| **S3** | UC Create-Managed-Delta from Python on each backend | ◐ Blocked on UC OSS upstream — `agent/tests/integration/test_create_table.py::test_create_table_local_fs` is skipped because UC OSS 0.4.0 does not implement `/api/2.1/unity-catalog/delta/preview/commits` (required by DuckDB's `unity_catalog` extension v0202409) and does not bootstrap the table's `_delta_log`. Cloud variants stay env-gated; re-enable when UC OSS ships coordinated-commits |
| **S4** | Memory cap + supervisor on the agent (OOM + wall-clock) | ◐ Partial — wall-clock passes; OOM / DuckDB interrupt not yet stress-tested (G-D2-a) |
| **S5** | UC credential vending end-to-end | ✓ Passed (local-fs/no-cred path) — `api/tests/integration/test_cred_vending.py`; S3/ADLS variants stay env-gated |

---

## 14. Open Questions

*All v0.2 questions still apply; revised standings:*

1. **Q1.** Does UC's Python client cover Create-Managed-Delta-Table for all
   backend kinds, or do we hand-roll REST? **Resolved (S3):** the
   `unitycatalog` SDK covers catalog/schema CRUD and table create with
   properties; `temporary-table-credentials` and other gaps are handled
   by raw `httpx` in `services/unity_catalog.py`.
2. **Q2.** UC credential-vending TTLs vs DuckDB's per-query lifetime — do
   we refresh mid-query, or size TTL conservatively? **Resolved (S5):**
   `services/uc_credentials.py` refreshes at half-TTL
   (`cred_safety_window_s` default = `max(300, vended_ttl_s / 2)`);
   per-query connections die before the cred does.
3. **Q3.** Result-set retention — 24h agent-local default; revisit after
   first user feedback. **Open — gated on G-D5-a implementation.**
4. **Q4.** Workspace deletion semantics — refuse (require manual cleanup).
   **MVP answer holds.**
5. **Q5.** Agent-restart policy on OOM. **Open — M4.**
6. **Q6.** Should saved queries pin an `agent_id` strictly, or just suggest
   the last-used one? **Decided — suggest.** `default_agent_id` on
   `saved_queries` is informational only.

---

## 15. Future Expansion

*Unchanged from v0.2.*

- **v1.x writes maturity:** SSE-streamed results, worksheet-driven
  `OPTIMIZE/VACUUM`, external **Quack endpoint** for BI clients with PAT
  auth.
- **v1.x DR for FS/NAS backends:** `restic` snapshots of the backend root
  to NAS or object storage.
- **v1.x observability:** `/metrics` Prometheus endpoints on the control
  plane and each agent; drop-in Grafana dashboard; per-agent dashboards.
- **v1.x agent labels + scoped routing** (builds on D17 capability
  advertisement): workspaces declare required labels; control plane filters
  the engine picker. Enables compliance ("eu-only agents") and class
  selection ("large-mem agents").
- **v2 UPDATE/MERGE/DELETE** when DuckDB ships them — widen the SQL
  allowlist (D8); no other changes.
- **v2 row-/column-level security** via query rewriting.
- **v2 notebook UI** if demand emerges; reuses D5's query lifecycle.
- **v2 heterogeneous engines** (Spark, Trino, Polars) — the agent contract
  was drawn so an additional engine type plugs in at D1/D16/D17 without
  changing the control plane's data model.
- **v2 per-table backend override** if users need hot/cold tiering inside
  a single workspace.
- **v3 control-plane HA** if a single box is outgrown — Postgres replica,
  UC HA, multiple FastAPI instances behind a load balancer.

---

## 16. Implementation Status & Known Gaps (new)

Per-decision summary (status from §4):

| Decision | Status | Notes |
|---|---|---|
| D1 dispatch-only control plane | ✓ | Wired end-to-end |
| D2 memory + timeout cap | ◐ | No DuckDB interrupt yet (G-D2-a) |
| D3 local auth | ✓ | bcrypt-12, sessions, admin seed script |
| D4 SQL worksheets only | ✓ | |
| D5 async query lifecycle | ✓ | Retention sweep pending (G-D5-a) |
| D6 pluggable storage backends | ✓ | Registry + workspace binding |
| D7 UC catalog + credential vendor | ✓ | UC client + half-TTL cred cache wired |
| D8 DDL via UC REST | ✓ | Schemas/tables endpoints + parse-only allowlist |
| D9 Catalog Commits ON | ✓ | `delta.feature.catalogManaged=supported` set on every duckhaven-created table |
| D10 workspace permissions | ✓ | UC mirror deferred to M4 (G-D10-a) |
| D11 audit log | ✓ | `queries.user_id` + `?user=` filter |
| D12 deployment | ◐ | Agent image build + `:latest` pin pending |
| D13 UI-first | ✓ | |
| D14 agent dial-home | ✓ | Result-server bearer plumbing pending |
| D15 user picks engine | ✓ | |
| D16 WS + Range protocol | ✓ | Bearer + progress persistence pending |
| D17 capability advertisement | ◐ | Heartbeat re-advertise + server-side compat check pending |
| D18 DR | ◐ | Script exists, cron + banner pending |

### Outstanding gaps tracker

| ID | Gap | Closes in |
|---|---|---|
| G-D2-a | Wire DuckDB query interrupt into supervisor; today only `asyncio.wait_for` fires | M4 |
| G-D2-b | Per-host operator ceiling cap on memory/timeout overrides | M4 |
| G-D5-a | 24h result-Parquet retention sweep on agent (`/var/duckhaven-agent/results`) | M4 |
| G-D10-a | UC permission mirror — replicate `workspace_members` to UC grants as defense-in-depth | M4 |
| G-D12-a | Build + publish a versioned `duckhaven-agent` image | ✓ Done — `.github/workflows/build.yml` builds + pushes both images on `v*.*.*` tags |
| G-D12-b | Pin `duckhaven-api` image to a version tag (drop `:latest`) | ✓ Done (M4) — compose pins `${DUCKHAVEN_API_IMAGE:-duckhaven-api:0.1.0}` |
| G-D14-a | Agent receives session token in `auth_ok` and configures its HTTP bearer from it | M4 |
| G-D16-a | Control-plane row-read proxy signs upstream requests with the agent bearer | M4 |
| G-D16-b | Persist `query_progress` so `GET /queries/{id}` can stream progress | M4+ |
| G-D17-a | Agent re-sends `agent_status` on each heartbeat (currently only on connect) | M4 |
| G-D17-b | `api/services/query.py` enforces backend-extension compatibility at dispatch (currently only the React `AgentPicker` checks) | M4 |
| G-D18-a | `scripts/pg-backup.sh` wired into cron / systemd timer | M4 |
| G-D18-b | Document second-disk target in runbook (script defaults to local path) | M4 |
| G-D18-c | Conditional DR banner in web UI for `local_fs` / `nas` workspaces | M4 |

---

*End of architecture document.*
