# Development Guide

## Prerequisites

- Python 3.14+
- `uv` (Python package manager)
- Node.js 22+
- `npm`
- Docker and Docker Compose (for integration tests)
- `make`

## Setup

```bash
# Clone the repository
git clone https://github.com/tamasmrtn/duckhaven.git
cd duckhaven

# Install all dependencies
make install
```

This runs `uv sync --all-packages` for Python and `npm install` in `web/`.

## Running Locally

### Option A: Full stack with Docker Compose

```bash
make compose-up
make dev-web
```

The first time you visit `http://localhost:8000` (or the Vite dev server at
`http://localhost:5173`), you'll land on the setup screen. Read the one-shot
token with:

```sh
docker compose -f deploy/docker-compose.yml cp api:/var/duckhaven/setup_token ./setup_token && cat ./setup_token
```

and paste it into the form to create the first admin.

The frontend dev server runs on `http://localhost:5173` and proxies API calls to `http://localhost:8000`.

### Option B: API only

```bash
# Requires Postgres running (e.g. via Docker)
make dev-api
```

Runs FastAPI with uvicorn reload on port 8000.

### Option C: Frontend only (with MSW mocks)

```bash
cd web
npm run dev
```

The frontend loads with Mock Service Worker handlers in development mode, so it works without a running backend.

## Running Tests

```bash
# All tests (unit + web)
make test

# API unit tests only
make test-api

# Agent unit tests only
make test-agent

# Web tests only
make test-web

# Integration tests (requires Postgres + Polaris)
make test-integration
```

Coverage thresholds: API ≥80%, agent ≥75%.

## Linting and Formatting

```bash
# Check everything
make lint

# Auto-fix where possible
make format
```

- Python: Ruff (lint + format)
- TypeScript: ESLint + Prettier

## Database Migrations

```bash
# Run pending migrations
make migrate

# Create a new migration after model changes
make migrate-new name="add users table"

# Downgrade one revision
make migrate-down
```

Migrations live in `api/alembic/versions/`.

## Container images

Both published images (`duckhaven-api`, `duckhaven-agent`) are built on
Chainguard's distroless Python, pinned by digest, in two stages: a builder
(`latest-dev`, which has a shell and `uv`) that resolves dependencies into
`/app/.venv`, and a runtime stage (`latest`) that copies only that virtualenv.
The API image adds a third stage that builds the SPA with `node:26-alpine`.

The runtime image has **no shell, no package manager, and no apt/dpkg**, and
runs as UID/GID `65532`. That is the whole hardening story now — as the
Dockerfile puts it, "the unfixable OS-package CVEs the old purge-and-upgrade
step chased don't exist here." An earlier iteration of these images was built
on `python:3.14-slim` and spent a `RUN` layer dropping `pip` and purging
`perl-base` to get the CVE count down; distroless removed the need for it.

One consequence worth knowing before you try: you cannot `docker exec` a shell
into a running container. See
[Debugging a running container](../operations/runbook.md) for the `docker debug`
route and the `-dev` tag.

### Building locally

```bash
docker build -f api/Dockerfile -t duckhaven-api:dev .
docker build -f agent/Dockerfile -t duckhaven-agent:dev .
```

Both builds must run from the repository root, because the Dockerfiles copy the
shared workspace manifests. Note that `.dockerignore` is a separate list from
`.gitignore`: a directory being gitignored does not keep it out of the build
context. Large local-only directories such as `benchmarks/` and
`deploy/terraform/` are excluded there explicitly — without those entries
BuildKit walks the whole tree and the build stalls before it runs a single step.
If you add a large generated directory to the repository, add it to
`.dockerignore` too.

## Project Structure

```
web/          React SPA frontend (Vite + React 19 + TypeScript)
api/          FastAPI control plane (Python 3.14)
agent/        DuckDB compute agent (Python 3.14)
shared/       Pydantic types shared by api and agent
deploy/       Docker Compose stack and env template
scripts/      Operator helper scripts
docs/         Architecture, design, and deployment docs
```

## Key Development Commands

| Command | Purpose |
|---|---|
| `make install` | Install all Python and Node dependencies |
| `make dev-api` | Run FastAPI with hot reload |
| `make dev-web` | Run Vite dev server |
| `make test` | Run all tests |
| `make lint` | Run all linters |
| `make format` | Auto-format all code |
| `make migrate` | Run Alembic migrations |
| `make compose-up` | Start Docker Compose stack |
| `make compose-logs` | Tail control plane logs |
| `make compose-pull` | Pull the latest published images |
| `make clean` | Remove caches, coverage, dist, node_modules |

## Writing Tests

### Frontend

- Framework: Vitest + React Testing Library + MSW
- Location: `web/tests/`
- Mock API handlers: `web/src/mock/handlers/`
- Every component and page should have a render test; interactive features need user-event tests.

### Backend

- Framework: pytest + pytest-asyncio
- API unit tests: `api/tests/unit/`
- Agent unit tests: `agent/tests/unit/`
- Integration tests: `api/tests/integration/` and `agent/tests/integration/`
- Use `AsyncClient` + `ASGITransport` for API tests.
- Override `get_db` and `get_polaris_client` in `conftest.py`.

## Conventional Commits

All commits must follow conventional commit format:

```
feat: add query cancellation
fix: resolve agent reconnect race
chore: bump ruff to 0.15
docs: update deployment guide
refactor: extract query dispatch service
test: add agent capability advertisement tests
```

Max 72 characters in the subject line. No trailing period.

Branch prefixes:

- `feat/` — new feature
- `fix/` — bug fix
- `chore/` — maintenance, deps, config
- `docs/` — documentation only
- `refactor/` — behavior-preserving restructure
- `test/` — test additions or corrections

See [CLAUDE.md](https://github.com/tamasmrtn/duckhaven/blob/main/CLAUDE.md) for the full development guidelines.
