# ── UI builder ─────────────────────────────────────────────────────────────
FROM node:26-alpine AS ui-builder

WORKDIR /web

# Install deps first so this layer caches as long as the manifests are unchanged.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ .
RUN npm run build
# Output lives in /web/dist (MSW is dev-only, excluded from the production build).

# ── Builder ──────────────────────────────────────────────────────────────────
# -dev keeps a shell for the build; runtime below is distroless. Pinned by
# digest since :latest rebuilds continuously (tracked by Dependabot).
FROM cgr.dev/chainguard/python:latest-dev@sha256:de669347e9c1348d3fb952b1ab93738412bd7207769a882701691825741bfce3 AS builder
USER root

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

# /var/duckhaven/secrets holds the generated secret_key. Pre-created here and
# copied into the runtime stage with --chown below — that stage has no shell
# to mkdir/chown in.
RUN mkdir -p /app/rootfs/var/duckhaven/secrets

# ── Runtime ──────────────────────────────────────────────────────────────────
# Distroless: no shell, package manager, or apt/dpkg — the unfixable
# OS-package CVEs the old purge-and-upgrade step chased don't exist here.
FROM cgr.dev/chainguard/python:latest@sha256:1f37785e5cdb70151f36aaa15e1e3cef4571424dbefbf4b0d8a9222535cb13ff AS runtime

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
COPY --chown=65532:65532 --from=builder /app/.venv /app/.venv

# The built SPA, served by FastAPI at / (see api.main, settings.static_dir).
COPY --chown=65532:65532 --from=ui-builder /web/dist /app/static

# Alembic migrations applied at container start by api.entrypoint.
COPY --chown=65532:65532 api/alembic.ini /app/alembic.ini
COPY --chown=65532:65532 api/alembic /app/alembic

# The documentation corpus, read by the assistant's read_doc_page tool (see
# settings.assistant_docs_dir). Last of the copies because Markdown churns most
# and would otherwise invalidate the migration and venv layers.
# Only the Markdown travels — .dockerignore holds back docs/images and friends.
COPY --chown=65532:65532 docs /app/docs

# Pre-created in the builder stage (see comment there) — no shell here to mkdir/chown.
COPY --chown=65532:65532 --from=builder /app/rootfs/var/duckhaven /var/duckhaven

USER 65532:65532

EXPOSE 8000

# api.entrypoint replaces the old shell script (see its docstring) and execs
# the CMD in place, so uvicorn gets SIGTERM directly as PID 1 — required for
# --timeout-graceful-shutdown to bound the drain correctly.
ENTRYPOINT ["python", "-m", "api.entrypoint"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
