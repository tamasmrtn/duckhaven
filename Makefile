.PHONY: install install-web dev dev-api dev-web \
        test test-api test-agent test-web \
        test-integration test-integration-api test-integration-agent \
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
	uv run --package duckhaven-api pytest api/tests/unit/ -v \
		--cov=api \
		--cov-config=pyproject.toml \
		--cov-report=term-missing \
		--cov-report=html:htmlcov/api \
		--cov-fail-under=80

test-agent:
	uv run --package duckhaven-agent pytest agent/tests/unit/ -v \
		--cov=agent \
		--cov-config=pyproject.toml \
		--cov-report=term-missing \
		--cov-report=html:htmlcov/agent \
		--cov-fail-under=75

test-web:
	cd web && npm run test

# ── Integration tests (opt-in; require UC + Postgres) ─────────────────────────
# Exit code 5 ("no tests ran") is tolerated so the targets are safe to run
# before the first integration test lands on a branch.
test-integration: test-integration-api test-integration-agent

test-integration-api:
	@uv run --package duckhaven-api pytest api/tests/integration/ -v -m integration; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

test-integration-agent:
	@uv run --package duckhaven-agent pytest agent/tests/integration/ -v -m integration; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

# ── Lint / Format ─────────────────────────────────────────────────────────────
lint:
	uv run ruff check api/src agent/src shared/src
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
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf htmlcov/ .coverage .coverage.*
	rm -rf web/dist web/node_modules
