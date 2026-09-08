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
COPY agent/pyproject.toml agent/
COPY shared/pyproject.toml shared/

# Install third-party dependencies only (no workspace members yet).
RUN uv sync --frozen --no-dev --no-editable --package duckhaven-agent --no-install-workspace

# Bring in workspace source and build + install the duckhaven-shared and
# duckhaven-agent wheels into /app/.venv (--no-editable: source not needed at runtime).
COPY shared/src shared/src
COPY agent/src agent/src
RUN uv sync --frozen --no-dev --no-editable --package duckhaven-agent

# Pre-install the DuckDB extensions the agent uses at runtime so the first query
# doesn't pay the download tax and `_get_capabilities` can advertise them on
# connect (closes G-D17-c). Extensions land in $HOME/.duckdb; HOME is pinned to
# the runtime user's home so the runtime stage resolves them with no download.
ENV HOME=/home/duckhaven
RUN mkdir -p /home/duckhaven && \
    /app/.venv/bin/python -c "import duckdb; c = duckdb.connect(); \
    [c.execute(f'INSTALL {ext}') for ext in ('httpfs','azure','iceberg')]; \
    [c.execute(f'LOAD {ext}') for ext in ('httpfs','azure','iceberg')]"

# Pre-created here since the runtime stage has no shell to mkdir/chown in;
# copied with --chown below.
RUN mkdir -p /var/duckhaven-agent/results

# ── Runtime ──────────────────────────────────────────────────────────────────
# Distroless: no shell, package manager, or apt/dpkg — the unfixable
# OS-package CVEs the old purge-and-upgrade step chased don't exist here.
FROM cgr.dev/chainguard/python:latest@sha256:1f37785e5cdb70151f36aaa15e1e3cef4571424dbefbf4b0d8a9222535cb13ff AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=1 \
    HOME=/home/duckhaven \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Only the populated virtualenv is needed at runtime.
COPY --chown=65532:65532 --from=builder /app/.venv /app/.venv

# Pre-installed DuckDB extensions, owned by the runtime user.
COPY --chown=65532:65532 --from=builder /home/duckhaven/.duckdb /home/duckhaven/.duckdb

# Pre-created in the builder stage (see comment there) — no shell here to mkdir/chown.
COPY --chown=65532:65532 --from=builder /var/duckhaven-agent/results /var/duckhaven-agent/results

USER 65532:65532

# Overrides the base's own ENTRYPOINT (its system python, not our venv's) so
# "python" resolves via PATH to /app/.venv/bin/python instead.
ENTRYPOINT ["python"]
CMD ["-m", "agent.main"]
