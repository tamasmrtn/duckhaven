.PHONY: install install-web dev dev-api dev-web \
        test test-api test-agent test-web \
        test-integration test-integration-api test-integration-agent \
        polaris-dev polaris-dev-down \
        lint format \
        migrate migrate-new migrate-down \
        compose-up compose-down compose-logs compose-pull \
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

# ── Integration tests (opt-in; require Polaris + Postgres) ────────────────────
# Exit code 5 ("no tests ran") is tolerated so the targets are safe to run
# before the first integration test lands on a branch.
test-integration: test-integration-api test-integration-agent

test-integration-api:
	@uv run --package duckhaven-api pytest api/tests/integration/ -v -m integration; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

test-integration-agent:
	@uv run --package duckhaven-agent pytest agent/tests/integration/ -v -m integration; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

# ── Local Polaris (for integration tests) ─────────────────────────────────────
# Spins up a single-container Apache Polaris with in-memory persistence and
# FILE storage, sharing a warehouse dir with the host so the agent test can
# read catalog metadata. Override the warehouse path or image tag if needed.
POLARIS_IMAGE_TAG ?= latest
POLARIS_WAREHOUSE_DIR ?= /tmp/duckhaven-warehouse

polaris-dev:
	mkdir -p $(POLARIS_WAREHOUSE_DIR) && chmod 777 $(POLARIS_WAREHOUSE_DIR)
	docker rm -f dh-polaris-dev >/dev/null 2>&1 || true
	docker run -d --name dh-polaris-dev \
		-p 8181:8181 -p 8182:8182 \
		-v $(POLARIS_WAREHOUSE_DIR):$(POLARIS_WAREHOUSE_DIR) \
		-e POLARIS_BOOTSTRAP_CREDENTIALS=POLARIS,root,s3cr3t \
		-e POLARIS_REALM_CONTEXT_REALMS=POLARIS \
		-e QUARKUS_OTEL_SDK_DISABLED=true \
		-e 'polaris.features."ALLOW_INSECURE_STORAGE_TYPES"=true' \
		-e 'polaris.features."SUPPORTED_CATALOG_STORAGE_TYPES"=["FILE"]' \
		-e polaris.readiness.ignore-severe-issues=true \
		apache/polaris:$(POLARIS_IMAGE_TAG)
	@echo "Waiting for Polaris to become healthy..."
	@for i in $$(seq 1 30); do \
		if curl -sf http://localhost:8182/q/health >/dev/null 2>&1; then echo "Polaris is up."; break; fi; \
		sleep 2; \
	done
	@echo ""
	@echo "Polaris ready on :8181. Run integration tests with:"
	@echo "  POLARIS_BASE_URL=http://localhost:8181 POLARIS_CLIENT_ID=root \\"
	@echo "  POLARIS_CLIENT_SECRET=s3cr3t POLARIS_WAREHOUSE_DIR=$(POLARIS_WAREHOUSE_DIR) \\"
	@echo "  make test-integration"

polaris-dev-down:
	docker rm -f dh-polaris-dev >/dev/null 2>&1 || true

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
# The compose stack auto-applies migrations and runs the browser-driven
# first-admin flow on first boot — `compose-up` is the whole install.
compose-up:
	docker compose -f deploy/docker-compose.yml up -d

compose-down:
	docker compose -f deploy/docker-compose.yml down

compose-logs:
	docker compose -f deploy/docker-compose.yml logs -f

compose-pull:
	docker compose -f deploy/docker-compose.yml pull

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf htmlcov/ .coverage .coverage.*
	rm -rf web/dist web/node_modules
