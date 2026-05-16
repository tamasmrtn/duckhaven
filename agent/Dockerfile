FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY agent/ agent/

RUN uv sync --package duckhaven-agent --no-dev

CMD ["uv", "run", "--package", "duckhaven-agent", "python", "-m", "agent.main"]
