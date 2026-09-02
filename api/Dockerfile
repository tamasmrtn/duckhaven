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

# Release version, injected from the git tag by CI (docker/metadata-action).
# Surfaced at GET /api/version and as OpenAPI info.version; defaults to a dev
# placeholder for local builds that pass no build-arg.
ARG DUCKHAVEN_APP_VERSION=0.0.0-dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DUCKHAVEN_APP_VERSION=$DUCKHAVEN_APP_VERSION

WORKDIR /app

# Only the populated virtualenv is needed at runtime.
COPY --from=builder /app/.venv /app/.venv

# The built SPA, served by FastAPI at / (see api.main, settings.static_dir).
COPY --from=ui-builder /web/dist /app/static

# The documentation corpus, read by the assistant's read_doc_page tool (see
# settings.assistant_docs_dir). Only the Markdown travels — docs/images is
# excluded in .dockerignore, which keeps ~9 MB of screenshots and the demo video
# out of the runtime layer. The pages are the same files mkdocs builds the site
# from, so there is no second copy to drift.
COPY docs /app/docs

# Alembic migrations applied at container start by api-entrypoint.sh.
COPY api/alembic.ini /app/alembic.ini
COPY api/alembic /app/alembic

# Entrypoint. On first boot it generates the app secret + setup_token, then
# reads them and constructs DATABASE_URL / SECRET_KEY at runtime (folds in the
# former init-secrets one-shot, so the api service prepares its own secrets).
COPY deploy/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh

# Hardening: drop pip, purge perl-base, patch the base image, run as a
# dedicated non-root user.
# perl-base is marked Essential but nothing in this image depends on it, and
# it carries 8 of the base image's CRITICAL/HIGH CVEs — all of them unfixable
# (Debian ships no patch). Purging it takes CRITICAL to zero. The forced flags
# are required only because of the Essential marker; apt still works after.
# /var/duckhaven is the api_data mount point; /var/duckhaven/secrets lives
# within it (holds the generated secret_key). Pre-creating the secrets dir
# owned by duckhaven lets the empty-volume init copy inherit the correct
# ownership.
RUN pip uninstall -y pip 2>/dev/null || true; \
    dpkg --purge --force-remove-essential --force-depends perl-base; \
    apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*; \
    useradd --system --create-home --home-dir /home/duckhaven duckhaven; \
    mkdir -p /var/duckhaven/secrets && chown -R duckhaven:duckhaven /var/duckhaven; \
    chmod +x /usr/local/bin/api-entrypoint.sh

USER duckhaven

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]
# --timeout-graceful-shutdown bounds the drain: on SIGTERM the lifespan marks the
# replica not-ready and hands its agents to peers, then in-flight requests get up
# to this long to finish before connections are force-closed.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
