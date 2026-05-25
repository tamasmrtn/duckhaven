FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY agent/ agent/

RUN uv sync --package duckhaven-agent --no-dev

# Pre-install the DuckDB extensions the agent uses at runtime so the
# first query doesn't pay the download tax and `_get_capabilities` can
# advertise them on connect (closes G-D17-c).
RUN uv run --package duckhaven-agent python -c "import duckdb; \
    c = duckdb.connect(); \
    [c.execute(f'INSTALL {ext}') for ext in ('httpfs','azure','unity_catalog','delta')]; \
    [c.execute(f'LOAD {ext}') for ext in ('httpfs','azure','unity_catalog','delta')]"

CMD ["uv", "run", "--package", "duckhaven-agent", "python", "-m", "agent.main"]
