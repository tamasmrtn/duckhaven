# DuckHaven — Operator Runbook

Operational procedures for the single control-plane box plus its agents.
Companion to `ARCHITECTURE.md` (§5 storage layout, §12 deployment, D14/D18).

---

## 1. Bring up the control plane

The control plane is one `docker compose` stack (`deploy/docker-compose.yml`:
`caddy`, `postgres`, `unity-catalog`, `api`).

1. Create `deploy/.env` with at least:
   ```sh
   POSTGRES_PASSWORD=<strong-password>
   SECRET_KEY=<random-32+ bytes>
   # Pin the API image (drops :latest, G-D12-b). For the CI-published image:
   DUCKHAVEN_API_IMAGE=ghcr.io/<owner>/duckhaven-api:0.1.0
   ```
   Images are published to GHCR by `.github/workflows/build.yml` on each
   `v*.*.*` git tag (both `duckhaven-api` and `duckhaven-agent`).
2. Start the stack: `make compose-up`.
3. Apply migrations: `make migrate` (runs `alembic upgrade head`).
4. Seed the first admin: `uv run python scripts/seed-admin.py --email you@host
   --password <pw> --name "You"`.
5. Caddy serves TLS (`tls internal`) on the Tailscale address only. There is no
   public ingress.

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
   CONTROL_PLANE_URL=wss://<control-plane-tailscale>/agents/connect
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
     counts per agent; **Admin → Audit** records agent/user/duration/rows).
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
- `local_fs` / `nas`: **no off-box DR** — the web UI shows a DR banner for
  these backends (G-D18-c). Ensure an independent backup of the backend root.

---

## 5. Tailscale outage

Tailscale is the only network path (R9). If it is down the platform is
unreachable. Document the agents' and control plane's static Tailscale IPs so
operators can confirm reachability; agents auto-reconnect (5 s backoff) once the
tailnet recovers.
