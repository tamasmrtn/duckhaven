# DuckHaven — Operator Runbook

Operational procedures for the single control-plane box plus its agents.
Companion to `ARCHITECTURE.md` (§5 storage layout, §12 deployment, D14/D18).

---

## 1. Bring up the control plane

The control plane is one `docker compose` stack (`deploy/docker-compose.yml`:
`postgres`, `polaris`, `api`). The `api` service publishes port `8000`
directly on the host.

1. (Optional) create `deploy/.env`. Defaults work — `POSTGRES_PASSWORD` and
   `SECRET_KEY` are generated on first boot and persisted to the `secrets`
   docker volume. Set values in `.env` only if you need to override them
   (e.g. pinning a release tag):

   ```sh
   DUCKHAVEN_IMAGE_TAG=v1.2.3
   ```

   Images are published to `ghcr.io/tamasmrtn/duckhaven-{api,agent}` by
   `.github/workflows/build.yml`: `:latest` on every main push, `:v1.2.3` /
   `:v1.2` / `:v1` on git tags. Built for `linux/amd64` and `linux/arm64`.
2. Start the stack: `make compose-up`. Migrations apply automatically.
3. Read the one-shot setup token:
   `docker compose -f deploy/docker-compose.yml exec api cat /var/duckhaven/setup_token`.
4. Open `http://<host>:8000` and create the first admin from the setup
   screen using the token.
5. The API listens on port `8000` on the Tailscale address only. There is no
   public ingress; the Tailscale/WireGuard tunnel encrypts the wire.

---

## 2. Register two agents (multi-agent M4 target)

Agents dial home with a one-time bootstrap token (D14); the control plane never
initiates connections.

For **each** agent host:

1. In the admin UI (**Admin → Agents → Generate bootstrap**) or via
   `SESSION_COOKIE=<cookie> scripts/gen-token.sh`, mint a bootstrap token
   (single-use, 24 h).
2. On the agent host, build/pull the agent image and set its `.env`:

   ```sh
   CONTROL_PLANE_URL=ws://<control-plane-tailscale>:8000/agents/connect
   BOOTSTRAP_TOKEN=<token-from-step-1>
   # Operator ceilings (non-overridable by per-query requests, G-D2-b):
   MAX_MEMORY_LIMIT_GB=6
   MAX_TIMEOUT_S=600
   RESULT_RETENTION_HOURS=24
   ```

3. Start the agent (`python -m agent.main`). It exchanges the bootstrap token
   for a long-lived `agent_session` credential, advertises its capabilities,
   and holds the WebSocket open.
4. Confirm both agents show **green** with fresh "last ping" in **Admin →
   Agents** (capabilities re-advertise on every heartbeat, G-D17-a).

Repeat so at least two agents are registered (e.g. one S3-capable, one local).

---

## 3. Exercise the engine selector under load (manual)

1. Open the worksheet; the engine picker lists both agents with their backend
   compatibility tags (✓ / ✗).
2. Run several queries, switching the selected agent per worksheet. Confirm:
   - Each query is dispatched to the chosen agent (Admin → Agents shows query
     counts per agent; **History** records agent/user/duration/rows).
   - Picking an agent that lacks the workspace backend's extension fails fast
     with an inline "missing `<ext>` extension" error (server-side check,
     G-D17-b).
   - A query that exceeds its timeout is interrupted on the agent and reported
     as `failed` (status "timeout"), not left running (G-D2-a).
3. Result range-reads are proxied with the agent's session bearer; a result
   that has aged past `RESULT_RETENTION_HOURS` is swept on the agent and the
   query is re-runnable from saved SQL (G-D5-a).

---

## 4. Backups & disaster recovery (D18)

### Schedule nightly Postgres backups (G-D18-a)

`scripts/pg-backup.sh` dumps the DuckHaven app state + UC metastore.

```sh
# Point backups at a SECOND disk / NAS mount, not the data disk (G-D18-b):
sudo cp deploy/systemd/duckhaven-backup.{service,timer} /etc/systemd/system/
# Edit WorkingDirectory and DUCKHAVEN_BACKUP_DIR in the .service first.
sudo systemctl enable --now duckhaven-backup.timer
systemctl list-timers duckhaven-backup.timer   # verify next run
```

`DUCKHAVEN_BACKUP_DIR` overrides the default `/var/duckhaven/backups`.

### Restore

```sh
gunzip -c <backup>.sql.gz | docker compose -f deploy/docker-compose.yml \
    exec -T postgres psql -U duckhaven duckhaven
```

### Data DR by backend kind

- `s3` / `adls_gen2`: delegated to the cloud provider's durability.
- `object_store` (bundled MinIO): **no off-box DR** — the web UI shows a DR
  banner for these backends (G-D18-c). Ensure an independent backup of the
  MinIO bucket.

---

## 5. Tailscale outage

Tailscale is the only network path (R9). If it is down the platform is
unreachable. Document the agents' and control plane's static Tailscale IPs so
operators can confirm reachability; agents auto-reconnect (5 s backoff) once the
tailnet recovers.

---

## 6. Query queueing & concurrency

Each agent runs queries under **admission control** so it never oversubscribes
its memory and gets OOM-killed: instead of picking up every dispatched query, it
admits queries up to a memory budget and **queues** the rest (FIFO). When a
running query finishes, the oldest queued query starts.

### How capacity is split

The agent's budget is `effective memory × (1 − headroom)` (cgroup limit when set,
else host RAM; headroom defaults to 10%, `MEMORY_HEADROOM_FRACTION`). There are
two ways to size each query's slice of that budget:

- **`auto` (the default)** — before running a query the agent runs `EXPLAIN` and
  estimates its peak memory from the optimizer's plan: the cardinality of
  *blocking* operators (joins, group-bys, sorts) times the row width, with a
  safety multiplier. The estimate snaps to a "T-shirt" bucket of the budget, so
  cheap queries reserve a small slice and pack in while heavy ones reserve more
  and queue when the agent is busy. Unestimable queries (DDL/DML, multi-statement,
  or an `EXPLAIN` failure/timeout) fall back to a default bucket. Estimation is
  best-effort under a short timeout and never delays or drops a query.
- **Static slot ladders** — the budget is divided into a fixed **weighted slot
  ladder**; a new query takes the largest free slot, so the first running query
  gets the most memory/threads and later ones get less.

| Profile        | Weights   | Slots | Share of budget                  |
| -------------- | --------- | ----- | -------------------------------- |
| `auto`         | —         | —     | per-query, from the EXPLAIN estimate |
| `single`       | `[1]`     | 1     | one query gets 100%              |
| `equal_2`      | `[1,1]`   | 2     | 50% / 50%                        |
| `decaying_2`   | `[2,1]`   | 2     | 67% / 33%                        |
| `decaying_3`   | `[3,2,1]` | 3     | 50% / 33% / 17%                  |

Default is `auto` (`MAX_CONCURRENCY_PROFILE`); the static ladders remain
selectable as fallbacks. The queue holds up to `MAX_QUEUE_DEPTH` (default 100)
queries; beyond that a query fails with `queue full`. `QUEUED_TIMEOUT_S`
(default 0 = off) fails a query that waits too long with `queued timeout`.
Whatever the mode, the agent enforces `Σ running memory_limit ≤ budget`, so it
never oversubscribes (DuckDB fixes a session's memory at start and cannot resize
a running query). Under a static ladder a lone query uses its slot's share, not
the whole box (only `single` gives one query the full budget); under `auto` a
query reserves the bucket its estimate maps to, up to the full budget.

#### Tuning the `auto` estimator

| Variable | Default | Description |
| --- | --- | --- |
| `ESTIMATE_SAFETY_MULTIPLIER` | `1.5` | Multiplies the raw EXPLAIN estimate to absorb under-estimation. |
| `ESTIMATE_FLOOR_BYTES` | `64 MiB` | Minimum reservation, so a tiny estimate still gets a usable slice. |
| `ESTIMATE_CEILING_FRACTION` | `1.0` | Caps a reservation at this fraction of the budget. |
| `EXPLAIN_TIMEOUT_S` | `2.0` | Time budget for the pre-run `EXPLAIN`; on timeout the query uses the fallback bucket. |
| `ESTIMATE_FALLBACK_BUCKET` | `M` | Bucket used when a query is unestimable (DDL/DML, multi-statement, EXPLAIN error/timeout). |

### Changing the profile from the worksheet

Run this DuckHaven control command (its own statement) in a worksheet:

```sql
SET duckhaven_concurrency = 'auto';   -- auto | single | equal_2 | decaying_2 | decaying_3
RESET duckhaven_concurrency;           -- back to the default (auto)
```

- It applies to the **agent currently selected** in that worksheet and is
  **agent-global**: it changes concurrency for *every* user's queries on that
  agent (like `ALTER WAREHOUSE`), not just your session.
- It takes effect for **future** admissions; already-running queries keep their
  slot. The setting is held in memory and **resets to the default on agent
  restart**.
- It is recorded in **History** like any query.

### Monitoring

**Admin → Utilization** shows two counters at the top — **Running queries** and
**Queued queries** (aggregated across agents) — plus each agent's active profile.
A persistently non-zero queued count means the agent is saturated: raise the
slot count (e.g. switch to `decaying_3`) only if per-query memory still suffices,
or add another agent.

---

## 7. Query profiles

After a query finishes, the agent captures DuckDB's per-operator execution
profile and ships it (KB-sized) to the control plane, where it is stored on the
query and served from `GET /queries/{id}/profile`. There are two ways to view it:

- **Worksheet → Profile tab** — an inline summary + collapsible operator tree
  for a quick glance at the query you just ran, with an **Open full profile**
  link to the dedicated page.
- **Dedicated profile page** (`/{ws}/queries/{id}`) — reached by clicking any
  row in **History**. It shows an interactive operator **graph** (result on top,
  scans at the bottom; data flows up) where clicking a node opens its detail.

Both surface:

- a summary strip — latency, CPU time, rows returned, result size, **peak
  memory**, the **reserved memory + CPU** the query ran under, **spill to disk**,
  and bytes read/written (so a spill is read against what was reserved);
- per-operator metrics — rows scanned → produced, bytes, a time-share bar, and
  the operator's `EXTRA_INFO` (join conditions, filters, group keys);
- **inefficiency highlights** computed from the profile — spilled queries
  (worth a larger reservation or less intermediate data), scan blow-ups (a scan
  reading far more rows than the query returns), bad cardinality estimates
  (actual far from the optimizer's `EXPLAIN` estimate), and time hotspots. The
  dedicated page also ranks the **most expensive operators** and lists the
  detected issues in a **diagnostics** panel, each linking to the offending node.

Profiling is on by default and best-effort: a capture failure yields no profile
rather than failing the query, and DDL/DML carry no profile (a no-profile state
is shown). Set `PROFILING_ENABLED=false` on the agent to disable it.

## 8. Recover a stuck or failed catalog storage migration

A [catalog storage migration](../guides/migrate-catalog-storage.md) is driven by the leader-elected migration runner;
all its state lives in Postgres (`catalog_migrations`, `catalog_migration_tables`, `catalog_migration_events`), so a
restart resumes an in-flight migration from its last completed table.

**Inspect.** Open **Admin → Migrations**, select the catalog, and read the live log, or query the tables directly:

```sql
SELECT id, status, tables_done, tables_total, error FROM catalog_migrations ORDER BY created_at DESC;
```

- **Stuck in `copying`/`verifying`.** Confirm the migration runner is enabled (`MIGRATION_RUNNER_ENABLED=true`) on at
  least one healthy replica and that the target backend is reachable (re-run its health check on the storage admin
  page). The runner retries transient storage/Polaris errors; a genuinely unreachable backend ends the migration as
  `failed`.
- **`failed`.** The catalog is untouched on its **original** backend (the pointer only changes at the atomic cutover).
  Read the `error` / log, fix the cause, and start a **new** migration. The failed run's shadow catalog is cleaned up
  best-effort; if an orphaned `<name>__m<hex>` Polaris catalog remains, drop it manually.
- **Cancel.** Cancelling before cutover tears the shadow copy down and leaves the catalog on its original backend.
- **Reverse a completed migration.** The old data is retained for `MIGRATION_RETENTION_DAYS` after cutover — start a new
  migration back to the original backend within that window.
