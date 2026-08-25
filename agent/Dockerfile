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

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=1 \
    HOME=/home/duckhaven \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Only the populated virtualenv is needed at runtime.
COPY --from=builder /app/.venv /app/.venv

# Hardening: drop pip, purge perl-base, patch the base image, run as a
# dedicated non-root user.
# perl-base is marked Essential but nothing in this image depends on it, and
# it carries 8 of the base image's CRITICAL/HIGH CVEs — all of them unfixable
# (Debian ships no patch). Purging it takes CRITICAL to zero. The forced flags
# are required only because of the Essential marker; apt still works after.
RUN pip uninstall -y pip 2>/dev/null || true; \
    dpkg --purge --force-remove-essential --force-depends perl-base; \
    apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*; \
    useradd --system --create-home --home-dir /home/duckhaven duckhaven

# Pre-installed DuckDB extensions, owned by the runtime user.
COPY --from=builder /home/duckhaven/.duckdb /home/duckhaven/.duckdb
RUN chown -R duckhaven:duckhaven /home/duckhaven

# Pre-create the results dir owned by the runtime user so an empty named volume
# mounted here inherits non-root ownership (the agent writes result Parquet as
# the duckhaven user).
RUN mkdir -p /var/duckhaven-agent/results && chown -R duckhaven:duckhaven /var/duckhaven-agent

USER duckhaven

CMD ["python", "-m", "agent.main"]
