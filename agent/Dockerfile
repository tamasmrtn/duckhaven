# ── Builder ──────────────────────────────────────────────────────────────────
# -dev keeps a shell for the build; runtime below is distroless. Pinned by
# digest since :latest rebuilds continuously (tracked by Dependabot).
FROM cgr.dev/chainguard/python:latest-dev@sha256:eb0d45dfc69fecb471d2eaee7a8eea281bf860578ef44cb85db1bfa8165c47fe AS builder
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

# Which runtime this image is (duckhaven_shared.runtimes): a DuckDB line plus its
# extension set. The default runtime's DuckDB is the one uv.lock resolved above;
# any other runtime overrides it here from its hash-locked pin, deliberately AFTER
# the frozen sync so every other dependency stays exactly as locked.
ARG DUCKHAVEN_RUNTIME=1.5
ARG DUCKHAVEN_APP_VERSION=0.0.0-dev
COPY agent/runtimes agent/runtimes
RUN if [ -f "agent/runtimes/${DUCKHAVEN_RUNTIME}.txt" ]; then \
        uv pip install --python /app/.venv/bin/python --require-hashes --no-deps \
            -r "agent/runtimes/${DUCKHAVEN_RUNTIME}.txt"; \
    fi

# Pre-install the runtime's DuckDB extensions so the first query doesn't pay the
# download tax and `_get_capabilities` can advertise them on connect (closes
# G-D17-c). Extensions land in $HOME/.duckdb; HOME is pinned to the runtime
# user's home so the runtime stage resolves them with no download. Required
# anyway: the agent's `internal: true` network cannot reach extensions.duckdb.org.
#
# The script fails the build if the DuckDB line is wrong or any extension does
# not load, and writes runtime.json: the identity the agent reports, baked into
# the image rather than read from the environment so it cannot be overridden.
ENV HOME=/home/duckhaven
COPY agent/docker/install_runtime.py /tmp/install_runtime.py
RUN mkdir -p /home/duckhaven && \
    /app/.venv/bin/python /tmp/install_runtime.py \
        "${DUCKHAVEN_RUNTIME}" "${DUCKHAVEN_APP_VERSION}" /app/runtime.json

# Pre-created here since the runtime stage has no shell to mkdir/chown in;
# copied with --chown below.
RUN mkdir -p /var/duckhaven-agent/results

# ── Runtime ──────────────────────────────────────────────────────────────────
# Distroless: no shell, package manager, or apt/dpkg — the unfixable
# OS-package CVEs the old purge-and-upgrade step chased don't exist here.
FROM cgr.dev/chainguard/python:latest@sha256:565af762d7f3efedc4e60d7ac7815e41588211d3f5757be33d8303e915ee6c72 AS runtime

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

# The runtime identity the agent reports (see install_runtime.py).
COPY --chown=65532:65532 --from=builder /app/runtime.json /app/runtime.json
ARG DUCKHAVEN_RUNTIME=1.5
LABEL org.duckhaven.runtime="${DUCKHAVEN_RUNTIME}"

# Pre-created in the builder stage (see comment there) — no shell here to mkdir/chown.
COPY --chown=65532:65532 --from=builder /var/duckhaven-agent/results /var/duckhaven-agent/results

USER 65532:65532

# Overrides the base's own ENTRYPOINT (its system python, not our venv's) so
# "python" resolves via PATH to /app/.venv/bin/python instead.
ENTRYPOINT ["python"]
CMD ["-m", "agent.main"]
