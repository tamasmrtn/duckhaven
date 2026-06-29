# HA failover runbook

Drills that prove the [highly-available control plane](../deployment/high-availability.md)
survives the failures it is designed for. Run them against a stack started with:

```bash
docker compose -f deploy/docker-compose.ha.yml --env-file deploy/.env up -d
```

All `docker compose` commands below assume that file. Give the stack a minute to
settle (`docker compose -f deploy/docker-compose.ha.yml ps` should show
`api-1`/`api-2` healthy and Patroni leader elected) before starting.

## Drill 1 — lose the Postgres primary

**Goal:** killing the current Postgres primary fails over to a standby and the API
recovers without a restart.

1. Find the leader:

   ```bash
   docker compose -f deploy/docker-compose.ha.yml exec patroni-1 patronictl list
   ```

   Note which node is `Leader`.
2. Confirm the API is healthy: `curl -fsS localhost:8000/api/readyz`.
3. Kill the leader (replace `patroni-1` with whichever is the leader):

   ```bash
   docker compose -f deploy/docker-compose.ha.yml kill patroni-1
   ```

4. Within a few seconds Patroni promotes the standby; `patronictl list` (from the
   surviving node) shows a new `Leader`, and HAProxy repoints `:5432` to it.
5. **Expected:** `/api/readyz` returns `200` again without restarting any API
   container — the connection pool's `pool_pre_ping` discards connections to the
   dead primary and re-opens against the new one. Run a query from the UI to
   confirm writes succeed.
6. Recover: `docker compose -f deploy/docker-compose.ha.yml start patroni-1` — it
   rejoins as a standby.

## Drill 2 — lose an API replica mid-query

**Goal:** killing the API replica that holds an agent's socket lets the agent
reconnect to another replica, and queries keep working.

1. Confirm both replicas are up and the agent is connected (**Admin → Agents**
   shows it healthy).
2. Identify which replica owns the agent socket by reading the `owner_url` column:

   ```bash
   docker compose -f deploy/docker-compose.ha.yml exec pg-haproxy \
     sh -c 'PGPASSWORD=$DUCKHAVEN_DB_PASSWORD psql -h 127.0.0.1 -U duckhaven duckhaven \
     -c "SELECT name, status, owner_url FROM agents;"'
   ```

   (`psql` is not in the HAProxy image; alternatively connect with any client to
   `pg-haproxy:5432`, database `duckhaven`, user `duckhaven`.)
3. Kill one replica:

   ```bash
   docker compose -f deploy/docker-compose.ha.yml kill api-1
   ```

4. **Expected:** the agent's WebSocket drops and it reconnects through Caddy,
   landing on `api-2`, which becomes the new owner (`owner_url` updates). Caddy
   stops routing browser traffic to `api-1` because `/api/readyz` no longer
   answers. Submit a new query — it dispatches via `api-2` and completes.
5. A query that was *running* on the agent at the instant of the kill may lose its
   final status frame (it is reaped by its timeout); its result Parquet still
   exists on the agent. New work is unaffected.
6. Recover: `docker compose -f deploy/docker-compose.ha.yml start api-1`.

For a **graceful** drain instead of a kill, use `stop` (sends SIGTERM): the
replica marks itself not-ready, hands its agents to the peer, and lets in-flight
requests finish before exiting.

## Drill 3 — two replicas don't double-run background work

**Goal:** with both replicas up, the maintenance scanner runs once per tick and
migrations don't race.

1. **Migrations:** both `api-1` and `api-2` run `alembic upgrade head` on boot.
   Confirm the schema is consistent and there were no duplicate-migration errors:

   ```bash
   docker compose -f deploy/docker-compose.ha.yml logs api-1 api-2 | grep -i alembic
   ```

   One replica applies pending migrations; the other blocks on the advisory lock
   and then finds the database already at head. There must be no
   "duplicate column"/"already exists" errors.
2. **Scanner:** trigger or wait for a scan cycle and confirm only one replica logs
   a run for a given tick:

   ```bash
   docker compose -f deploy/docker-compose.ha.yml logs api-1 api-2 | grep "Maintenance scan:"
   ```

   The leader logs `Maintenance scan: {...ran...}`; the standby stays silent
   (it loses the advisory lock that tick). A given table is probed once, not
   twice. (These module logs require the root logger to be configured, which it
   is by default; set `LOG_LEVEL=INFO` if you've raised it.)

   If you'd rather not wait the default tick, lower it via
   `MAINTENANCE_SCAN_TICK_S` (the HA compose passes it through). Independent of
   logs, you can confirm the loop is advancing by watching `maintenance_policy`
   in Postgres — `last_scan_at` moves forward on its own once a cycle is due, and
   never jumps twice for the same tick:

   ```sql
   SELECT last_scan_at, scan_cursor FROM maintenance_policy;
   ```

3. **Scheduler:** the query [scheduler](../guides/schedule-queries.md) uses the same
   leader-election pattern (a distinct advisory lock). With an enabled schedule due,
   confirm only one replica logs a dispatch for a given tick:

   ```bash
   docker compose -f deploy/docker-compose.ha.yml logs api-1 api-2 | grep "Scheduled run:"
   ```

   The leader logs `Scheduled run: ...`; the standby stays silent. A due schedule
   produces exactly one run per tick, not one per replica. Independent of logs, the
   schedule's `last_run_at` / `last_run_query_id` advance once per fired run and never
   twice for the same tick:

   ```sql
   SELECT id, enabled, next_run_at, last_run_at FROM schedules;
   ```

## After any drill

Check overall health:

```bash
docker compose -f deploy/docker-compose.ha.yml ps
curl -fsS localhost:8000/api/readyz && echo OK
```

Both API replicas should be healthy, the Postgres cluster should have a leader and
a running standby, and `/api/readyz` should return `200`.
