# Quickstart

Stand up the full DuckHaven control plane with one `docker compose` stack and run your first query. The bundled stack is
Postgres, Apache Polaris, MinIO (object storage), the DuckHaven API (which serves both the REST API and the web UI on
port 8000), and a bundled DuckDB agent.

## Prerequisites

- Linux host with Docker Engine 24+ and Docker Compose v2
- 8 GB RAM minimum
- (Recommended) Tailscale or another private network for ingress

## 1. Install

```bash
curl -O https://raw.githubusercontent.com/tamasmrtn/duckhaven/main/deploy/docker-compose.yml
docker compose up -d
```

That is the whole install — no `git clone`, no `.env` editing, no `make` on the host. On first boot the stack
auto-generates `POSTGRES_PASSWORD`, `SECRET_KEY`, and a one-shot first-admin setup token, and applies the database
migrations automatically.

## 2. Create the first admin

Read the one-shot setup token on the host:

```bash
docker compose exec api cat /var/duckhaven/setup_token
```

Open `http://<host>:8000` in a browser. The app detects an empty database and routes you to the setup screen — paste the
token, pick admin credentials, and submit. The token is consumed (deleted) after the admin is created and is not
regenerated on subsequent boots.

!!! tip "Starting over"
    To wipe the stack and its data, run `docker compose down -v`. This removes Postgres, the secrets, and the setup
    token.

## 3. Run your first query

1. Sign in with the admin account you just created.
2. Open a **Worksheet** and pick the bundled agent from the engine picker.
3. Type a SQL statement and run it with **Ctrl+Enter** (or the Run button):

   ```sql
   SELECT 42 AS answer;
   ```

4. Inspect the results grid. After the query finishes, open the **Profile** tab to see the per-operator execution
   profile.

The allowed SQL surface is data statements (`SELECT`/`INSERT`/`UPDATE`/`DELETE`/`MERGE`) and catalog DDL
(`CREATE`/`ALTER`/`DROP`), executed on the agent against the Polaris catalog. Sandbox escapes such as `ATTACH`, `COPY`,
`LOAD`, and `SET` are rejected at the API boundary.

## Next steps

- [Add an agent](../deployment/add-agent.md) — scale compute by registering more DuckDB agents.
- [Reverse proxy + TLS](../deployment/reverse-proxy-tls.md) — front the stack with Caddy or Nginx Proxy Manager.
- [Backup and restore](../deployment/backup-restore.md) — protect Postgres and the secrets volume.
- [Configuration reference](../reference/configuration.md) — every environment variable, in one place.
- [Architecture](../concepts/architecture.md) — how the control plane and agents fit together.
