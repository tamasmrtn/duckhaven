# DuckHaven

A self-hosted SQL workspace and federation control plane over Delta Lake tables governed by Unity Catalog.

## Quick start

```sh
cp deploy/.env.example deploy/.env
# edit deploy/.env — set POSTGRES_PASSWORD and SECRET_KEY

make install
make compose-up
make migrate
make dev-web
```

## Structure

| Directory | Contents |
|---|---|
| `api/` | FastAPI control plane |
| `agent/` | DuckDB compute agent |
| `shared/` | Python types shared by api and agent |
| `web/` | React + TypeScript + Vite frontend |
| `deploy/` | docker-compose stack, Caddyfile, env template |
| `scripts/` | Operator helper scripts |

## Common tasks

```sh
make test          # run all tests
make lint          # ruff + mypy + eslint
make migrate       # run pending Alembic migrations
make migrate-new name="add users table"
make compose-logs  # tail control-plane logs
```
