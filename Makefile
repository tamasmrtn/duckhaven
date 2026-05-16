.PHONY: install install-web dev dev-api dev-web \
        test test-api test-agent test-web \
        lint format \
        migrate migrate-new migrate-down \
        compose-up compose-down compose-logs compose-build \
        clean

# ── Dependencies ──────────────────────────────────────────────────────────────
install:
	uv sync --all-packages
	cd web && npm install

install-web:
	cd web && npm install

# ── Development ───────────────────────────────────────────────────────────────
dev-api:
	uv run --package duckhaven-api uvicorn api.main:app --reload --port 8000

dev-web:
	cd web && npm run dev

dev:
	$(MAKE) compose-up
	$(MAKE) dev-web

# ── Tests ─────────────────────────────────────────────────────────────────────
test: test-api test-agent test-web

test-api:
	uv run --package duckhaven-api pytest api/tests/ -v

test-agent:
	uv run --package duckhaven-agent pytest agent/tests/ -v

test-web:
	cd web && npm run test

# ── Lint / Format ─────────────────────────────────────────────────────────────
lint:
	uv run ruff check api/src agent/src shared/src
	uv run mypy api/src agent/src shared/src
	cd web && npm run lint

format:
	uv run ruff format api/src agent/src shared/src
	cd web && npm run format

# ── Database migrations ───────────────────────────────────────────────────────
migrate:
	uv run --package duckhaven-api alembic -c api/alembic.ini upgrade head

migrate-new:
	uv run --package duckhaven-api alembic -c api/alembic.ini revision --autogenerate -m "$(name)"

migrate-down:
	uv run --package duckhaven-api alembic -c api/alembic.ini downgrade -1

# ── Docker / deployment ───────────────────────────────────────────────────────
compose-up:
	docker compose -f deploy/docker-compose.yml up -d

compose-down:
	docker compose -f deploy/docker-compose.yml down

compose-logs:
	docker compose -f deploy/docker-compose.yml logs -f

compose-build:
	docker compose -f deploy/docker-compose.yml build

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf web/dist web/node_modules
