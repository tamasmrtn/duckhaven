# DuckHaven — Architecture

> **Context for this revision.** The previous RFC (v0.1) described a
> single-box, single-DuckDB-process system rooted at a local filesystem.
> DuckHaven's purpose has shifted: it is now a **control plane** that data
> engineers use to compose a stack of **registerable DuckDB compute agents**
> over **pluggable storage backends** (local FS, NAS, S3, ADLS Gen 2). The
> control plane itself remains a single self-hosted box; only compute and
> storage become distributed. This document is the architecture for that
> revised system. Where a previous decision is preserved verbatim, it carries
> its original number; where it is replaced, the old number is reused with the
> new content (and the rationale notes the supersession).
>
> **Status:** RFC v0.2 — pre-implementation. Subject to revision after the
> validation spikes (§13) but stable enough to build the first MVP milestone
> against.
>
> **Audience:** the engineer(s) implementing DuckHaven.

---

## 1. What DuckHaven Is

A self-hosted, browser-based SQL workspace and **federation control plane**
over Delta Lake tables governed by Unity Catalog. DuckHaven's control plane
runs on a single homelab-class box on a Tailscale network; the compute and
storage it dispatches against are explicitly distributed.

| | |
|---|---|
| Control-plane unit | One `docker compose` stack on one Linux box |
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

Two facts shape every decision below: (a) **the user picks the engine per
worksheet** — DuckHaven is dispatcher, not optimizer; (b) **each workspace is
bound to exactly one storage backend** — DuckHaven enforces this at
workspace-create time and at every write.

---

## 2. Non-Goals (explicit)

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
- No cross-workspace joins in MVP (each workspace is one backend; cross-backend
  reads require credentials we have explicitly scoped down).
- No agent autoscaling, no agent-level cost-based routing. Users pick.

---

## 3. System Diagram

```
                ┌──────────────────────────────────────────────┐
   Browser ─────┤   Caddy (TLS internal)                       │
   (Tailscale)  └─────────────────┬────────────────────────────┘
                                  │  HTTPS
                ┌─────────────────▼────────────────────────────┐
                │  duckhaven-api  (FastAPI)  — control plane   │
                │  • auth, workspaces, queries, audit          │
                │  • agent registry + dispatcher               │
                │  • storage-backend registry + UC bindings    │
                │  • DDL via UC REST                           │
                └─┬───────────────┬─────────────────┬──────────┘
                  │               │                 │
        ┌─────────▼─────┐ ┌───────▼────────┐ ┌──────▼─────────┐
        │ Postgres      │ │ unity-catalog  │ │ Agent control  │
        │ users / ws /  │ │  REST :8080    │ │ channel (WS)   │
        │ queries / etc │ │  + credential  │ │ — agents dial  │
        │               │ │    vending     │ │   home         │
        └───────────────┘ └────────┬───────┘ └──────┬─────────┘
                                   │                │
                                   │     ┌──────────┴────────────────┐
                                   │     │                            │
                                   │     ▼                            ▼
                          ┌────────┴────────┐             ┌────────────────────┐
                          │ duckhaven-agent │             │ duckhaven-agent    │
                          │  (VM / host A)  │   ...       │  (VM / host N)    │
                          │ • DuckDB ≥1.5   │             │ • DuckDB ≥1.5     │
                          │ • UC + delta    │             │ • UC + delta      │
                          │ • httpfs+azure  │             │ • httpfs+azure    │
                          │ • per-query cap │             │ • per-query cap   │
                          │ • result cache  │             │ • result cache    │
                          └──────┬──────────┘             └──────┬─────────────┘
                                 │ short-lived creds (from UC)   │
                       ┌─────────▼────────┐ ┌──────────────┐ ┌───▼────────────┐
                       │ Local FS / NAS   │ │  S3 bucket   │ │ ADLS Gen 2     │
                       │ /var/dh/data/... │ │              │ │ abfss://...    │
                       └──────────────────┘ └──────────────┘ └────────────────┘
                          (workspace-pinned: each workspace → exactly one backend)
```

---

## 4. Architectural Decisions

Each decision is presented in **Decision / Context / Consequences** form.

---

### D1 — Federated DuckDB agents; control plane is dispatcher only *(supersedes v0.1 D1)*

**Decision.** Compute lives in **`duckhaven-agent`** processes, each
embedding one DuckDB engine, running on a separate host/VM from the control
plane. The control plane (`duckhaven-api`) **does not run DuckDB**; it
dispatches queries to a chosen agent over a job-dispatch RPC. The user
selects the agent per worksheet (see D15).

**Context.** Decoupling control from compute makes hardware decisions
elastic (add a beefy agent for one team without resizing the box that holds
Postgres/UC), supports heterogeneous DuckDB versions side-by-side (e.g. an
agent pinned to an older extension build), and removes the single-process
OOM blast radius. DuckDB-only keeps the engine matrix tractable for MVP.

**Consequences.**
- The control plane's resource footprint is small and predictable — no DuckDB
  there, only Postgres + UC + FastAPI.
- The agent ↔ control-plane wire is **not Quack** in MVP. Agents embed DuckDB
  directly; the wire is a small JSON/RPC protocol over WebSocket (see D16).
  Quack as an externally exposed BI endpoint is a v1.x consideration.
- Cross-agent atomicity does not exist. A `BEGIN/COMMIT` lives in one agent.
- Agent crash kills its in-flight queries; clients see them as `failed` and
  may retry on another agent.
- See Risk **R1** and Spike **S1**.

---

### D2 — Per-query memory cap + wall-clock timeout, enforced at the agent

**Decision.** Every query, before execution, sets `memory_limit` (default
`6GB`) and `statement_timeout` (default `10min`). The **agent** owns the
supervisor and the DuckDB interrupt. Defaults are workspace-overridable;
operator-set ceilings on the agent are non-overridable.

**Context.** Even with isolated agents, one bad query can OOM one agent and
take its workspace's in-flight queries down. The cap is the cheapest possible
defense and lets operators size agents with confidence.

**Consequences.**
- Heavy queries beyond the cap fail cleanly rather than crashing the agent.
- Agent-side operator ceiling prevents a workspace owner from raising the
  cap above what the host can survive.
- Supervisor uses DuckDB's query interrupt API.

---

### D3 — Local user table + bcrypt + sessions; no external IdP *(unchanged)*

**Decision.** Identity lives in `users(id, email, password_hash[bcrypt-12+],
…)`. Sessions are HTTP-only `Secure` cookies, SameSite=Lax. A
`credentials(id, user_id, kind[session|pat|agent_bootstrap], …)` table exists
from day 1. The `agent_bootstrap` kind is new (see D14).

**Context.** Tailscale provides the network perimeter. With ≤10 trusted users,
an IdP (Authelia/Keycloak) is overhead without value. PATs and agent
bootstrap tokens share the credential machinery.

**Consequences.**
- No SSO; password resets are operator-managed.
- One credential table covers user sessions, future PATs, and agent
  bootstrap/refresh tokens.
- If exposure ever changes from Tailscale-only, revisit this decision *before*
  opening the firewall.

---

### D4 — SQL worksheets only; no notebooks in MVP *(unchanged)*

**Decision.** The frontend offers a Monaco-based SQL editor + result grid +
saved queries + table browser + **engine selector**. No cell model. No
Markdown cells. No Python kernel.

**Context.** Notebook UX is ~5× the engineering of worksheets and would
expand the platform into kernel-management territory.

**Consequences.**
- Frontend is small and sharp.
- A future notebook UI (v2) can reuse the worksheet's async query lifecycle
  (D5) without backend rework.

---

### D5 — Async query lifecycle; results materialized on the executing agent *(updated from v0.1 D5)*

**Decision.**
- `POST /workspaces/{ws}/queries` with `{sql, agent_id}` →
  `{id, status:'queued'}`.
- The control plane writes the query row to Postgres and pushes a dispatch
  message to the chosen agent's WebSocket channel.
- The agent executes locally, materializes the result to
  `/var/duckhaven/results/{query_uuid}.parquet` **on the agent**, and reports
  back `{status, row_count, duration_ms, result_path}`.
- `GET /queries/{id}` → metadata from Postgres.
- `GET /queries/{id}/rows?cursor=…` → control plane proxies a ranged read
  from the agent that holds the result.
- `DELETE /queries/{id}` → cancel signal forwarded to the agent.

**Context.** Shipping result Parquet back to the control plane on every
query wastes bandwidth, doubles disk usage on the control-plane box, and
makes the control plane a hot egress path. Letting the result live on the
agent that produced it keeps the data plane symmetric to where the compute
ran. The control plane only proxies pagination reads.

**Consequences.**
- Postgres remains the query-state-of-record. No Redis.
- Result lifetime is bounded by **agent uptime**. Agent restart drops cached
  results; affected queries are re-runnable from saved SQL. Acceptable.
- Default 24h retention, swept by an agent-local cleanup job.
- Pagination latency adds one control-plane hop on top of the agent's local
  Parquet read. Acceptable at MVP query sizes.
- See Risk **R5**.

---

### D6 — Pluggable storage backends; per-workspace binding *(supersedes v0.1 D6)*

**Decision.** A workspace is bound to **exactly one** storage backend at
create time. Supported backends in MVP:

| Backend kind | URI prefix | DuckDB extension | Mount story |
|---|---|---|---|
| `local_fs` | `file:///var/duckhaven/data/...` | none | Path mounted on every agent that serves the workspace |
| `nas` | `file:///mnt/<name>/...` | none | NFS/SMB mounted on every agent that serves the workspace |
| `s3` | `s3://bucket/prefix/...` | `httpfs` | Network — no mount |
| `adls_gen2` | `abfss://container@account/...` | `azure` | Network — no mount |

`local_fs` and `nas` are the same code path inside DuckDB; they are kept as
distinct kinds in the registry only so the operator UI can show "this is on
the box" vs "this is on the NAS" and surface mount-readiness checks.

The control plane holds a **`storage_backends`** registry: `(id, kind,
root_uri, uc_storage_credential_id, created_by, …)`. Workspaces reference a
backend by id.

**Context.** v0.1 baked a `/var/duckhaven/data` assumption into every layer;
the new platform vision requires data to live where the operator chooses —
including cloud object stores — without reshaping DuckHaven.

**Consequences.**
- Workspace creation requires choosing a backend; backend choice is
  immutable after creation (workspace deletion + recreate to move).
- Agents must have the right DuckDB extensions (`httpfs`, `azure`) baked in
  to serve cloud-backed workspaces. The registry records which agent ↔
  backend pairs are healthy (see D17).
- Per-workspace binding gives a tractable credential matrix: an agent needs
  exactly the credentials for the backends it has been asked to serve.
- Cross-workspace joins are unsupported in MVP (D10) because they'd cross
  credential domains.
- See Risk **R8**.

---

### D7 — Unity Catalog OSS, used both as catalog and as credential vendor

**Decision.** Unity Catalog OSS runs as a JVM container as before. **One UC
catalog per DuckHaven workspace.** UC additionally holds
`storage_credentials` and `external_locations` for every workspace backend.
At query dispatch time, the control plane requests **short-lived credentials**
from UC scoped to the workspace's backend and forwards them to the agent.

**Context.** UC's credential-vending pattern is the OSS equivalent of what
Databricks does and keeps long-lived cloud secrets out of agent images. It
also tightens the blast radius: a stolen agent credential expires within
minutes.

**Consequences.**
- 8 GB RAM control-plane floor is dominated by UC's JVM footprint (~2 GB
  resident).
- Workspace permission model maps natively to UC catalog grants (D10).
- The control plane is on the **hot path** for query dispatch (must fetch
  credentials per query). Credentials are cached per (agent, workspace) for
  a fraction of their TTL. See Risk **R3**.
- Catalog drift between DuckHaven's `workspace_members` and UC grants is a
  real risk → **R4** and v1.1 reconciliation cron.

---

### D8 — DuckHaven owns DDL via UC REST; DuckDB on agents executes DML *(unchanged)*

**Decision.** Table creation (`CREATE TABLE`) is performed by `duckhaven-api`
calling Unity Catalog's REST API directly with the workspace's backend as
the table's storage root. INSERT and SELECT run on a selected agent. UPDATE/
MERGE/DELETE are out of scope until DuckDB ships support.

**Context.** As of May 2026, the DuckDB `unity_catalog` extension supports
SELECT, INSERT, INSERT…SELECT, BEGIN/COMMIT, time travel — but not CREATE
TABLE, UPDATE, MERGE, or DELETE against UC catalogs.

**Consequences.**
- A "Create Table" UI action calls a DuckHaven endpoint, which calls UC REST,
  which registers a Catalog-Managed Delta table (see D9) at the workspace's
  configured backend root.
- Worksheet SQL is restricted to `SELECT` + `INSERT` (+ `INSERT…SELECT`) at
  parse time. A SQL allowlist is enforced in `duckhaven-api`; non-allowed
  statements are rejected before they reach an agent.
- When DuckDB ships UPDATE/MERGE/DELETE, the allowlist is widened.

---

### D9 — Catalog Commits ON for every DuckHaven-managed table *(unchanged)*

**Decision.** Every table created through DuckHaven's Create-Table flow has
`TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')`. UC arbitrates
all writes; conflicting writers receive a conflict error and retry.

**Consequences** are unchanged. See R7.

---

### D10 — Workspace-level permissions; permissions and storage co-pinned *(updated)*

**Decision.** Permissions are workspace-scoped: `workspace_members(workspace_id,
user_id, role[owner|writer|reader])`. DuckHaven enforces this at the API
boundary; UC grants are mirrored as defense-in-depth. Because each workspace
is also bound to exactly one backend (D6), workspace membership is the only
authorization decision for both compute access and storage access.

**Context.** Per-table permissions would multiply with per-table backends if
we ever moved off per-workspace binding; collapsing both axes to workspace
keeps the model compact.

**Consequences.**
- Permission decisions are O(1) per query (membership lookup).
- Cross-workspace queries unsupported in MVP.
- A workspace owner can do anything in their workspace, including drop tables.

---

### D11 — Audit log: who/when/SQL/agent/duration/rows/status *(updated)*

**Decision.** `audit_queries(id, user_id, workspace_id, agent_id, sql,
status, started_at, finished_at, row_count, duration_ms, error)` is written
by `duckhaven-api` for every query. `agent_id` is new vs v0.1.

**Consequences.**
- Operators can answer "is one agent producing all the slow queries?"
  without joining tables.
- Standard Postgres indexing; monthly partitioning if it ever grows large.

---

### D12 — Deployment: control plane as `docker compose`; agents as separate processes *(updated from v0.1 D12)*

**Decision.** Control-plane services live in one `docker-compose.yml` with
pinned image tags: `caddy`, `postgres`, `unity-catalog`, `duckhaven-api`.
Agents are **separate** — a single container image (`duckhaven-agent`) that
the operator deploys onto each compute host (VM, bare metal, separate
homelab box). The agent is configured only with a control-plane URL and a
one-time bootstrap token (D14).

**Context.** Operators decide where compute lives. Some will keep one agent
on the control-plane box (single-machine deployments are still valid); others
will spin up a beefy VM. The deployment story stays "compose for the brain,
one binary for each muscle".

**Consequences.**
- Image tags **never** `:latest`.
- Postgres + UC migrations run on control-plane container start.
- Agents auto-update by re-pulling and reconnecting — operator-driven.
- Logs to stdout from each component.

---

### D13 — UI-first frontend; mocked backend during M1 *(unchanged)*

**Decision.** After a one-week walking spike (M0) that validates the agent
↔ control-plane dispatch end-to-end, the frontend is built against a mock
backend implementing §6. The real backend (M2) is implemented to satisfy
that contract.

**Consequences.** Unchanged from v0.1.

---

### D14 — Agents dial home with a one-time bootstrap token; control plane never initiates connections *(new)*

**Decision.** Operator generates a bootstrap token in the DuckHaven admin
UI. Token is single-use, scoped to a label-free agent identity, valid for
24h. The agent starts with the token + the control-plane URL, opens an
outbound WebSocket to `/agents/connect`, exchanges the bootstrap token for a
long-lived **agent credential** (`credentials.kind = 'agent_session'`), and
holds the WebSocket open for the rest of its life.

**Context.** Agents may live behind NAT, on a different Tailscale node, or
inside a hypervisor where inbound is awkward. Outbound-only join makes
deployment a one-liner and removes any need for the control plane to
discover agent reachability.

**Consequences.**
- The agent's identity in Postgres has a stable id but no preassigned
  hostname — the WebSocket is the addressable handle.
- A disconnected agent is marked `unavailable`; queries targeting it fail
  fast with a clear error.
- Bootstrap-token reuse is impossible (single-use); rotation of the
  long-lived credential is a v1.x feature.
- See Risk **R2**.

---

### D15 — User picks the executing agent per worksheet/query *(new)*

**Decision.** Every worksheet has an **engine selector** populated from the
agents the user's workspaces can address. Each query carries an `agent_id`.
Any registered agent can serve any workspace (no labels in MVP).

**Context.** DuckHaven is a dispatcher, not an optimizer. Users know their
own cost/perf tradeoffs better than a heuristic would in MVP. Engine
labelling/scoping is in §15 for v2+.

**Consequences.**
- The control plane verifies the agent is healthy and the user is a member
  of the workspace; it does **not** verify the agent has the right
  extensions for the workspace's backend ahead of time — that check is done
  at dispatch (see D17).
- Saved queries record the agent the user last ran them on (informational;
  the user can change it).

---

### D16 — Agent control protocol: WebSocket dispatch, HTTP result range reads *(new)*

**Decision.** Two channels between control plane and agent:

1. **Control channel** — one long-lived WebSocket per agent (initiated by
   the agent). Frames: `dispatch_query`, `query_progress`, `query_done`,
   `cancel_query`, `heartbeat`, `agent_status`. JSON; small payloads only.
2. **Result-read channel** — control plane issues `GET` requests to the
   agent's local HTTP endpoint (signed with the agent credential) for
   ranged reads of `results/{uuid}.parquet`. The agent listens on this HTTP
   port only on `127.0.0.1` or its Tailscale address (operator's choice).

Quack is **not** the wire between control plane and agent in MVP. (It may
be re-introduced in v1.x as the **external** BI-client endpoint exposed by
the control plane.)

**Context.** The original v0.1 motivation for Quack was a clean
control/data-plane split; the agent model makes that split structural at
the deployment level, so Quack-as-internal-RPC is no longer load-bearing.

**Consequences.**
- One process boundary, two protocols, both simple.
- Spike S1 validates the control-channel cancellation and error semantics
  before M0 exits.

---

### D17 — Agent capability advertisement; dispatch-time backend compatibility check *(new)*

**Decision.** On connect (and at every heartbeat), an agent advertises a
small **capabilities document**: DuckDB version, list of loaded extensions
(`unity_catalog`, `delta`, `httpfs`, `azure`, …), per-query memory ceiling,
and host info. The control plane stores the latest doc per agent. At query
dispatch time the control plane checks that the agent's extensions cover
the workspace's backend kind; mismatches reject the query with a
remediation hint.

**Context.** Operators will run agents with varied extension sets. Without
this check, users get cryptic DuckDB errors when they pick an agent that
cannot talk to the workspace's backend.

**Consequences.**
- Agent images can be lean (filesystem-only agents need no `httpfs`).
- v2's label/scope feature builds on this without schema change.

---

### D18 — DR: nightly `pg_dump`; data DR is delegated to backend *(updated from v0.1 D14)*

**Decision.** Control-plane Postgres (which holds both DuckHaven app state
and UC's metastore) is `pg_dump`'d nightly to a second disk. For data DR,
the workspace's backend kind dictates the story:

- `local_fs` / `nas`: same disclaimer as v0.1 — user accepts disk failure
  as a total-data-loss event; v1.x adds `restic` snapshots.
- `s3` / `adls_gen2`: DR is the cloud provider's responsibility; DuckHaven
  surfaces the backend's redundancy class in the workspace UI.

**Consequences.**
- The DR banner now reads conditionally: only workspaces backed by
  `local_fs`/`nas` show the loud "may cause data loss" copy.
- Catalog metadata loss is still cheap to protect against (`pg_dump`).

---

## 5. Storage Layout (canonical)

```
# CONTROL-PLANE BOX
/var/duckhaven/                         # control plane only
  postgres/                             # Docker volume — app state + UC metastore
  unity_catalog/                        # Docker volume — UC's other state
  caddy/                                # Docker volume — TLS material

# EACH AGENT HOST
/var/duckhaven-agent/
  results/                              # Materialized query results (D5)
    {query_uuid}.parquet                # Default 24h retention, agent-local
  cache/                                # Optional DuckDB http/object-store cache
  agent.toml                            # Control-plane URL, agent credential
  mounts/                               # NAS/FS mounts (operator-configured)

# BACKEND ROOTS (workspace-pinned; example layouts)
file:///var/duckhaven/data/{workspace}/{schema}/{table}/_delta_log/
file:///mnt/nas01/{workspace}/{schema}/{table}/_delta_log/
s3://my-bucket/duckhaven/{workspace}/{schema}/{table}/_delta_log/
abfss://container@acct/duckhaven/{workspace}/{schema}/{table}/_delta_log/
```

---

## 6. API Contract (sketch — definitive in OpenAPI)

```
# Auth & users (unchanged)
POST   /auth/login                   {email, password}             → sets cookie
POST   /auth/logout
GET    /me                                                         → user

# Workspaces — now backend-bound
GET    /workspaces                                                 → [workspace]
POST   /workspaces                   {slug, name, kind, storage_backend_id}
                                                                   → workspace
GET    /workspaces/{ws}                                            → workspace
GET    /workspaces/{ws}/members
POST   /workspaces/{ws}/members      {user_id, role}

# Storage backends (admin)
GET    /admin/storage-backends                                     → [backend]
POST   /admin/storage-backends       {kind, root_uri, uc_storage_credential_id, ...}
DELETE /admin/storage-backends/{id}                                → only if no ws uses it

# Agents (admin + read-only for picker)
GET    /admin/agents                                               → [agent + caps]
POST   /admin/agents/bootstrap                                     → {token, expires_at}
GET    /agents                                                     → [agent for engine picker]
WS     /agents/connect                (agent-initiated, bootstrap or session token)

# Schemas / tables (unchanged shape; backend is implicit from ws)
GET    /workspaces/{ws}/schemas
POST   /workspaces/{ws}/schemas      {name}
GET    /workspaces/{ws}/schemas/{s}/tables
POST   /workspaces/{ws}/schemas/{s}/tables   {name, columns:[{name,type,nullable}]}
                                                                   → UC REST: CC Delta on ws backend
DELETE /workspaces/{ws}/schemas/{s}/tables/{t}

# Queries — now agent-routed
POST   /workspaces/{ws}/queries      {sql, agent_id, [memory_limit, timeout]}
                                                                   → {id, status:'queued'}
GET    /queries/{id}                                               → metadata (incl. agent_id)
GET    /queries/{id}/rows?cursor&limit                             → proxied range read from agent
DELETE /queries/{id}                                               → cancel signal to agent

GET    /workspaces/{ws}/saved-queries
POST   /workspaces/{ws}/saved-queries   {name, sql, default_agent_id}

GET    /admin/audit?workspace=&user=&agent=&since=&until=          → audit rows
```

---

## 7. Resource Budget

### Control-plane box (8 GB floor)

| Component | Resident | Notes |
|---|---|---|
| Linux + page cache | ~1.0 GB | |
| Caddy | ~50 MB | |
| Postgres 16 | ~600 MB | DuckHaven app + UC metastore |
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

Agents are sized independently; a 32 GB box gives ~24 GB of usable query
budget per agent.

---

## 8. Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript + Vite, Monaco editor, TanStack Query, shadcn/ui |
| API | FastAPI (Python 3.14), async; `websockets` for agent channel |
| ORM / migrations | SQLAlchemy 2.x + Alembic |
| Job dispatch | Direct WebSocket push to chosen agent; Postgres holds query state of record (no Redis) |
| Agent runtime | Python process embedding DuckDB; small HTTP server for result-range reads |
| Engine | DuckDB ≥ 1.5.x (pinned minor) — present **only** on agents |
| Catalog | Unity Catalog OSS ≥ 0.4.0 (pinned), used as catalog + credential vendor |
| Catalog client | `unitycatalog` Python client + raw REST for gaps |
| Storage format | Delta Lake, Catalog Commits ON (D9), per-workspace backend (D6) |
| Storage drivers (in agent) | DuckDB native (FS), `httpfs` (S3), `azure` (ADLS Gen 2) |
| Reverse proxy | Caddy 2 (`tls internal`) |
| Identity | Local users + bcrypt + cookies (D3); agent sessions via WS |
| Networking | Tailscale |
| Container | docker compose v2 (control plane); single container for agents |
| Observability | JSON logs to stdout from every component + built-in audit UI |

---

## 9. Security Model

- **Network perimeter:** Tailscale. No public ingress. Caddy `tls internal`.
- **Authentication (users):** D3.
- **Authentication (agents):** D14 — bootstrap token → long-lived agent
  credential; WebSocket bound to that credential; HTTP result endpoint signed
  with the same credential.
- **Authorization:** D10. Every query is validated for `workspace_members`
  before dispatch.
- **Backend credentials:** D7 — UC vends short-lived storage credentials per
  query; agents never hold long-lived cloud secrets in MVP. Local FS and NAS
  rely on the agent host's filesystem permissions.
- **Sandboxing (agents):** agent process has read+write only on
  `/var/duckhaven-agent/` and the backend roots its workspaces require. No
  outbound network beyond control plane + backends + UC.
- **Rate limiting:** login endpoint only, in-memory per-IP, 5 failed/min.
- **Secrets:** UC token, Postgres password, agent bootstrap secret held in
  `.env` outside the repo, consumed by docker compose. No vault.
- **In threat model:** misclick / accidental destructive query, cookie theft
  on a shared device, leaked agent credential (short TTL on backend creds
  limits damage).
- **Out of threat model:** RCE in DuckDB extensions, internet-borne
  attackers (Tailscale removes them), malicious DuckHaven users or operators.

---

## 10. MVP Scope (definitive)

**In scope.**
- 2–10 local user accounts; sessions; per-user/per-group workspaces +
  shared "public".
- Storage-backend registry (admin UI): create/delete local-FS, NAS, S3, ADLS
  backends.
- Workspace bound to one backend at create time.
- Agent registry (admin UI): generate bootstrap tokens; view connected
  agents and their capabilities; revoke.
- One reference agent image with all four storage extensions baked in.
- Catalog browser: workspace → schema → table → columns.
- SQL worksheet: editor, **engine selector**, run, cancel, results
  (paginated), saved queries.
- Async query lifecycle with agent-local materialized result Parquet (D5).
- Per-query memory cap + wall-clock timeout on agents (D2).
- "Create Table" UI action → UC REST → Catalog-Managed Delta table on the
  workspace's backend (D8/D9).
- `INSERT` / `INSERT…SELECT` from worksheets (D8).
- Audit query log with `agent_id` (D11) + admin UI page.
- `docker compose` deployment for control plane (D12); single container for
  agents; Caddy + Tailscale-only.
- Conditional DR banner (D18); nightly `pg_dump` cron.

**Out of MVP (explicit).**
- `UPDATE / MERGE / DELETE` (waiting on DuckDB).
- Notebooks, kernels, visualizations beyond the result grid.
- External Quack endpoint / PATs.
- Lineage; column-/row-level security.
- Cross-workspace joins; per-table backend overrides.
- Agent labels / scoped routing (D17 advertises capabilities; matching
  workspaces to label sets is v2).
- Materialized views, scheduled queries, jobs.
- OIDC; vault-based secret management.

---

## 11. Milestones

| M | Goal | Exit criterion |
|---|---|---|
| **M0 — Walking spike (1 wk)** | One hardcoded SELECT through control plane + one agent. | A SELECT against a hand-loaded Delta table returns rows via the WebSocket dispatch + range-read path from a real agent attached to a real UC. Validates D1/D7/D9/D14/D16. Throwaway. |
| **M1 — UX on mocked backend (3–4 wk)** | High-fidelity React UI against a mock implementing §6, including the engine selector and backend registry. | All MVP screens usable. UX validated by 2–5 users. Mock contract documented. |
| **M2 — Backend wiring (3–4 wk)** | Real FastAPI + Postgres + agent control protocol satisfy the M1 contract. One agent, local FS only. | End-to-end SELECT works through real backend. Query lifecycle (D5) persists. Auth (D3, D14) works. |
| **M3 — Catalog + writes (2–3 wk)** | Create Table via UC REST against any of the four backend kinds; INSERT from worksheet. | Two users create + INSERT into a table in two workspaces backed by different kinds (local FS + S3); Catalog Commits validated under concurrent INSERTs (D9). |
| **M4 — Multi-agent + hardening (2 wk)** | Two registered agents; engine selector exercised; production-ready single-control-plane box. | Memory caps verified under stress (D2); credential vending under load (D7); DR banner conditional; nightly `pg_dump`; runbooks; image tags pinned. |

**Total estimated wall time: 11–14 weeks.** Single engineer, part-time,
with the five spikes (§13) done before M0.

---

## 12. Risks

| ID | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | Agent control protocol (WS + range reads) has rough edges at scale | M | M | Validated in S1; small protocol, JSON only; cancel/error paths covered by tests |
| R2 | Dial-home flow breaks behind aggressive NAT / firewalls | L | M | Tailscale removes most NAT; runbook covers MTU + proxy traversal |
| R3 | UC credential vending becomes the bottleneck on hot dispatch | M | M | Cache vended creds per (agent, workspace) for ~½ TTL; pre-warm on workspace open |
| R4 | DuckHaven ↔ UC grant drift | M | M | v1.1 reconciliation cron; integration tests on member-change paths |
| R5 | Agent crash mid-query loses materialized results | M | L | Results are re-runnable from saved SQL; D5 marks queries failed cleanly |
| R6 | DuckDB UC extension write path has rough edges on cloud backends | M | H | Spikes S3/S5 cover write paths on each backend; pin extension version |
| R7 | INSERT against a non-CC table corrupts data | M | H | DuckHaven refuses INSERT against non-CC tables (D9) |
| R8 | Operator misconfigures a workspace backend (bad creds, wrong region) | M | M | UC `external_locations` validated at workspace-create; surface clear errors |
| R9 | Tailscale outage = total platform outage | L | H | Document static-IP fallback in runbook |
| R10 | SSD failure on FS/NAS-backed workspace → data loss | L | C | Conditional UI banner (D18); v1.x `restic` |
| R11 | Catalog metadata loss | L | H | Nightly `pg_dump` (D18) |
| R12 | Pure UI-first sequencing forces backend rework | H | M | M0 spike grounds the contract; document mocks rigorously |

---

## 13. Validation Spikes (do these before M0)

Each spike is 1–2 days of throwaway code. Failure of any spike forces a
design revision before M0.

1. **S1 — Agent control protocol.** Run a control plane stub and an agent
   stub. Open WebSocket, dispatch a hardcoded SELECT, stream completion,
   cancel a long-running query, simulate agent disconnect. *Pass criterion:*
   clean cancel, clean disconnect→requeue→fail, errors carry SQL line/col.
2. **S2 — UC OSS + DuckDB attach (local FS).** Bring up UC OSS in compose,
   register a Delta table via the CLI, ATTACH from DuckDB in an agent, run
   SELECT and INSERT. *Pass criterion:* both work; INSERT visible in UC.
3. **S3 — UC Create Table from Python on each backend.** Call UC REST to
   create a managed CC Delta table on local FS, then S3, then ADLS.
   *Pass criterion:* each table is writable from DuckDB via the
   `unity_catalog` extension on a properly-extensioned agent.
4. **S4 — Memory cap + supervisor on the agent.** Run a pathological
   cross-join; confirm DuckDB returns a clean OOM at the cap; confirm
   supervisor wall-clock kill works without leaking process state.
   *Pass criterion:* both kill paths work; subsequent queries unaffected.
5. **S5 — UC credential vending end-to-end.** Configure a `storage_credential`
   in UC for S3; via control plane, request short-lived creds for a workspace
   bound to that credential; hand them to the agent; run an INSERT.
   *Pass criterion:* the agent never holds the long-lived AWS key; the
   short-lived credential expires as expected; renewal works mid-session.

---

## 14. Open Questions (must resolve during M0/M1)

1. **Q1.** Does UC's Python client cover Create-Managed-Delta-Table for all
   backend kinds, or do we hand-roll REST? *Resolved by S3.*
2. **Q2.** UC credential-vending TTLs vs DuckDB's per-query lifetime — do we
   refresh mid-query, or size TTL conservatively? *Resolved by S5.*
3. **Q3.** Result-set retention — 24h agent-local default; revisit after
   first user feedback.
4. **Q4.** Workspace deletion semantics — drop UC catalog + delete data
   files at the backend, or archive? MVP: refuse; require manual cleanup.
5. **Q5.** Agent-restart policy on OOM — `unless-stopped` with backoff, or
   supervised process inside the container?
6. **Q6.** Should saved queries pin an `agent_id` strictly, or just suggest
   the last-used one? Default: suggest.

---

## 15. Future Expansion (post-MVP, non-binding)

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
  UC HA, multiple FastAPI instances behind Caddy.

---

*End of architecture document.*
