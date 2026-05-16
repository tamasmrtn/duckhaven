FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY api/ api/

RUN uv sync --package duckhaven-api --no-dev

CMD ["uv", "run", "--package", "duckhaven-api", \
     "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
