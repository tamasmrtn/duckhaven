# High availability

By default DuckHaven runs a **single-node control plane** — one Postgres, one
Polaris, one API container — which is simple to operate and fine for most
installs. The trade-off is a single failure domain: if that Postgres or API
container goes down, everyone is down until it comes back.

This page describes the **opt-in highly-available topology**: HA Postgres plus
multiple API replicas behind a load balancer, so the control plane survives the
loss of a database node or an API container. It runs entirely on Docker Compose
(no Kubernetes). Compute [agents](../concepts/agents.md) already scale
horizontally and are unaffected.

!!! note "This is opt-in"
    HA lives in a separate file, `deploy/docker-compose.ha.yml`. The single-node
    `deploy/docker-compose.yml` is unchanged. Nothing here is required for a
    normal install.

## What becomes highly available

| Tier | Single-node | HA topology |
|---|---|---|
| Postgres | one container | Patroni cluster (streaming replication + automatic failover) behind HAProxy, **or** a managed database |
| API | one uvicorn process | N replicas behind Caddy, health-checked on `/api/readyz` |
| Agents | already horizontal | unchanged |
| Polaris | one container | one container (stateless on Postgres; replicate the same way if needed) |

## How it works

The API was almost stateless already — user sessions and agent credentials live
in Postgres, and query results are fetched directly from the executing agent over
HTTP. Two things needed coordinating to run more than one replica:

**Agent dispatch.** An agent's WebSocket is pinned to whichever replica it dialed,
but a query can be created on any replica. Each replica records, on the agent's
row, that it owns that socket (`owner_url`). When a replica needs to send a frame
to an agent it doesn't hold locally, it forwards the frame over the private
`/internal` API to the owning replica, which puts it on the socket. Result frames
already flow back through Postgres, so only this outbound hop is added. No Redis
or other broker is involved.

**Background work.** The maintenance scanner and database migrations would
otherwise run on every replica at once. Both are now serialized with Postgres
advisory locks: the scanner elects one leader per tick, and `alembic upgrade head`
takes a lock so concurrent replicas migrate one-at-a-time (the rest find the
database already at head). This is why you can leave
`MAINTENANCE_SCANNER_ENABLED=true` on every replica.

**Draining.** On shutdown a replica marks itself not-ready (so the load balancer
stops routing to it), closes its agent sockets so those agents reconnect to a
live replica immediately, and lets in-flight requests finish before exiting.

## Configure it

Set a few values in `deploy/.env` (see [Configuration](../reference/configuration.md#high-availability)):

```bash
# A fixed app secret shared by every replica (so cookies/tokens verify anywhere):
SECRET_KEY=<long-random-string>
# Shared secret for the private cross-replica dispatch API:
INTERNAL_API_SECRET=<another-long-random-string>
# Postgres cluster passwords:
DUCKHAVEN_DB_PASSWORD=<app-db-password>
POSTGRES_SUPERUSER_PASSWORD=<superuser-password>
POSTGRES_REPLICATION_PASSWORD=<replication-password>
```

Then bring up the HA stack:

```bash
docker compose -f deploy/docker-compose.ha.yml --env-file deploy/.env up -d
```

This starts: `etcd` + `patroni-1/2` + `pg-haproxy` (HA Postgres), `polaris` +
`minio`, `api-1` + `api-2`, `caddy` (load balancer on `:8000`), and the bundled
`agent`. The API is reachable on `http://localhost:8000`.

### First-admin onboarding under HA

The one-time setup token that gates first-admin creation (`POST /api/setup/admin`)
is written to **each replica's own data volume** and checked against the local
file. Because Caddy round-robins, the browser setup flow can land on the replica
whose token you didn't read. The reliable path is to create the admin against one
replica directly, bypassing the load balancer:

```bash
docker compose -f deploy/docker-compose.ha.yml exec -T api-1 python - <<'PY'
import httpx
token = open("/var/duckhaven/setup_token").read().strip()
r = httpx.post("http://localhost:8000/api/setup/admin",
    json={"email": "admin@example.com", "password": "<password>", "name": "Admin"},
    headers={"X-Setup-Token": token}, timeout=30)
print(r.status_code, r.text[:200])
PY
```

The user database is shared, so once created the admin can sign in through Caddy
on any replica.

### API replicas behind Caddy

Caddy is the single entrypoint and load balancer. It round-robins across the API
replicas and actively health-checks `/api/readyz`, so a replica that is draining
or has lost a dependency is pulled out of rotation:

```caddyfile
:80 {
    @internal path /internal/*
    respond @internal 404            # never expose the private dispatch API

    reverse_proxy api-1:8000 api-2:8000 {
        lb_policy round_robin
        health_uri /api/readyz
        health_interval 5s
    }
}
```

No session affinity is needed: browser sessions are validated against Postgres on
every request, and agent dispatch is routed by ownership rather than stickiness.
WebSocket upgrades (agents dial `/agents/connect`) are proxied transparently. Each
replica gets a unique `REPLICA_ID` and `REPLICA_INTERNAL_URL` so peers can reach
it; the `/internal` endpoints are refused at the load balancer and only used
replica-to-replica on the private network.

To add a third replica, copy the `api-2` service to `api-3` (its own
`REPLICA_ID`/`REPLICA_INTERNAL_URL` and data volume) and add `api-3:8000` to the
Caddy upstream list.

### HA Postgres with Patroni + HAProxy

[Patroni](https://patroni.readthedocs.io/) runs Postgres with streaming
replication and automatic failover, using `etcd` to elect the primary. HAProxy
sits in front and exposes **one** read-write endpoint (`pg-haproxy:5432`) by
health-checking each node's Patroni REST API — only the current leader answers
`GET /primary` with `200`:

```
listen postgres_write
    bind *:5432
    option httpchk OPTIONS /primary
    http-check expect status 200
    default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
    server patroni-1 patroni-1:5432 check port 8008
    server patroni-2 patroni-2:5432 check port 8008
```

Because the API connects to `pg-haproxy`, its `database_url` never changes when
the primary moves. The API's connection pool uses `pool_pre_ping`, so a pooled
connection to a demoted primary is discarded and re-opened against the new
primary on the next checkout — failover is transparent to application code.

!!! warning "Quorum in production"
    The bundled HA file uses a single `etcd` and two Patroni nodes to stay light
    enough to run on one host for evaluation. A real deployment should use **3
    etcd** and **3 Postgres** nodes across separate hosts/AZs so a single node
    loss still leaves a quorum, and should spread services with an overlay
    network or per-host compose files.

### Or: a managed database

If you run on a cloud, the simplest HA Postgres is a **managed** one — point
`database_url` (via `POSTGRES_HOST`) at the managed endpoint and drop the
`etcd`/`patroni`/`pg-haproxy` services entirely:

| Cloud | Managed Postgres | API replicas + LB |
|---|---|---|
| AWS | RDS / Aurora **Multi-AZ** | ECS/EC2 replicas behind an **ALB** (WebSocket-aware) |
| Azure | Database for PostgreSQL **Flexible Server**, zone-redundant | Container Apps / VMSS behind **Application Gateway** |
| GCP | Cloud SQL **HA** | Cloud Run / MIG behind **Cloud Load Balancing** |

The application changes are identical in all cases; only the database and load
balancer are provider-managed. Use a managed L7 load balancer with WebSocket
support in place of Caddy, and give each replica a service-discoverable
`REPLICA_INTERNAL_URL` on the private network.

## Verify failover

See the [HA failover runbook](../operations/ha-failover.md) for the drills:
killing the Postgres primary, killing an API replica mid-query, and confirming
the scanner and migrations don't double-run across replicas.

## Limitations

- **In-flight queries on a hard replica kill.** If a replica is killed
  (not drained) while it holds the socket for a running query, that query's final
  status frame can be lost; the result Parquet still exists on the agent and the
  query is reaped by its timeout. New queries are routed to a live replica
  immediately. A graceful stop (SIGTERM) drains cleanly.
- **Live agent metrics** are a best-effort view aggregated across replicas; a
  replica that is momentarily unreachable is simply omitted from the metrics
  panel until it responds.
- **Polaris** is run single-instance here. It is stateless on Postgres and can be
  replicated behind the load balancer the same way, but that is outside this
  guide's scope.
