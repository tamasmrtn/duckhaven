# ── UI builder ─────────────────────────────────────────────────────────────
FROM node:24-alpine AS ui-builder

WORKDIR /web

# Install deps first so this layer caches as long as the manifests are unchanged.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ .
RUN npm run build
# Output lives in /web/dist (MSW is dev-only, excluded from the production build).

# ── Builder ──────────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

# uv is a build-time tool — copy the pinned binary instead of pip-installing it.
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependency manifests first so the third-party dep layer caches independently
# of source changes.
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/
COPY shared/pyproject.toml shared/

# Install third-party dependencies only (no workspace members yet).
RUN uv sync --frozen --no-dev --no-editable --package duckhaven-api --no-install-workspace

# Bring in workspace source and build + install the duckhaven-shared and
# duckhaven-api wheels into /app/.venv (--no-editable: source not needed at runtime).
COPY shared/src shared/src
COPY api/src api/src
RUN uv sync --frozen --no-dev --no-editable --package duckhaven-api

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Only the populated virtualenv is needed at runtime.
COPY --from=builder /app/.venv /app/.venv

# The built SPA, served by FastAPI at / (see api.main, settings.static_dir).
COPY --from=ui-builder /web/dist /app/static

# Migration assets and the admin-seed script, run in-container against the DB
# (see `make compose-migrate` / `make compose-seed`). The DB URL is read from
# the container's DATABASE_URL via api.config.settings.
COPY api/alembic.ini /app/alembic.ini
COPY api/alembic /app/alembic
COPY scripts/seed-admin.py /app/scripts/seed-admin.py

# Hardening: drop pip, patch the base image, run as a dedicated non-root user.
# /var/duckhaven is the api_data mount point (see deploy/docker-compose.yml).
RUN pip uninstall -y pip 2>/dev/null || true; \
    apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*; \
    useradd --system --create-home --home-dir /home/duckhaven duckhaven; \
    mkdir -p /var/duckhaven && chown duckhaven:duckhaven /var/duckhaven

USER duckhaven

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
