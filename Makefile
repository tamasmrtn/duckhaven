.PHONY: install install-web dev dev-api dev-web \
        test test-api test-agent test-web test-deploy \
        test-integration test-integration-api test-integration-agent \
        test-cross-component test-e2e \
        polaris-dev polaris-dev-s3 polaris-dev-down \
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
test: test-api test-agent test-web test-deploy

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

test-deploy:
	uv run --package duckhaven-api pytest deploy/tests/ -v

# ── Integration tests (opt-in; require Polaris + Postgres) ────────────────────
# Exit code 5 ("no tests ran") is tolerated so the targets are safe to run
# before the first integration test lands on a branch.
test-integration: test-integration-api test-integration-agent test-cross-component

test-integration-api:
	@uv run --package duckhaven-api pytest api/tests/integration/ -v -m integration; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

test-integration-agent:
	@uv run --package duckhaven-agent pytest agent/tests/integration/ -v -m integration; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

# Cross-component (Layer 2): boots the real API + agent over the live control
# channel. Needs Postgres + Polaris + MinIO (DATABASE_URL, POLARIS_BASE_URL,
# POLARIS_S3_*); skips cleanly when unset.
test-cross-component:
	@uv run pytest tests/cross_component/ -v -m cross_component; \
	  rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi

# End-to-end (Layer 3): Playwright against the full compose stack. Bring it up
# first (`make compose-up`) and export DH_SETUP_TOKEN for a fresh stack:
#   export DH_SETUP_TOKEN="$$(docker compose -f deploy/docker-compose.yml \
#     exec -T api cat /var/duckhaven/setup_token)"
test-e2e:
	cd e2e && npm ci && npx playwright install --with-deps chromium && npx playwright test

# ── Local Polaris (for integration tests) ─────────────────────────────────────
# Spins up MinIO + Apache Polaris-on-S3 (in-memory persistence). Object storage
# is the only storage DuckHaven uses: Polaris vends scoped credentials so the
# agent's DuckDB can read AND write Iceberg tables. Override the bucket or image
# tag if needed.
POLARIS_IMAGE_TAG ?= latest
POLARIS_S3_BUCKET ?= warehouse

polaris-dev:
	docker network create dh-polaris-net >/dev/null 2>&1 || true
	docker rm -f dh-polaris-dev dh-minio-dev >/dev/null 2>&1 || true
	docker run -d --name dh-minio-dev --network dh-polaris-net -p 9000:9000 \
		-e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
		minio/minio server /data
	@echo "Waiting for MinIO..."
	@for i in $$(seq 1 20); do \
		curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1 && break; sleep 1; \
	done
	docker run --rm --network dh-polaris-net \
		-e AWS_ACCESS_KEY_ID=minioadmin -e AWS_SECRET_ACCESS_KEY=minioadmin -e AWS_REGION=us-east-1 \
		amazon/aws-cli:2.34.48 --endpoint-url http://dh-minio-dev:9000 s3 mb s3://$(POLARIS_S3_BUCKET) || true
	docker run -d --name dh-polaris-dev --network dh-polaris-net \
		-p 8181:8181 -p 8182:8182 \
		-e POLARIS_BOOTSTRAP_CREDENTIALS=POLARIS,root,s3cr3t \
		-e POLARIS_REALM_CONTEXT_REALMS=POLARIS \
		-e AWS_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=minioadmin -e AWS_SECRET_ACCESS_KEY=minioadmin \
		-e QUARKUS_OTEL_SDK_DISABLED=true \
		-e 'polaris.features."SUPPORTED_CATALOG_STORAGE_TYPES"=["S3"]' \
		-e 'polaris.features."ALLOW_INSECURE_STORAGE_TYPES"=true' \
		-e polaris.readiness.ignore-severe-issues=true \
		apache/polaris:$(POLARIS_IMAGE_TAG)
	@echo "Waiting for Polaris to become healthy..."
	@for i in $$(seq 1 30); do \
		if curl -sf http://localhost:8182/q/health >/dev/null 2>&1; then echo "Polaris is up."; break; fi; \
		sleep 2; \
	done
	@echo ""
	@echo "Polaris (S3/MinIO) ready on :8181. Run integration tests with:"
	@echo "  POLARIS_BASE_URL=http://localhost:8181 POLARIS_CLIENT_ID=root POLARIS_CLIENT_SECRET=s3cr3t \\"
	@echo "  POLARIS_S3_BUCKET=s3://$(POLARIS_S3_BUCKET) POLARIS_S3_ENDPOINT=http://localhost:9000 \\"
	@echo "  POLARIS_S3_ENDPOINT_INTERNAL=http://dh-minio-dev:9000 make test-integration"

# Backwards-compatible alias for the now-default MinIO+S3 stack.
polaris-dev-s3: polaris-dev

polaris-dev-down:
	docker rm -f dh-polaris-dev dh-minio-dev >/dev/null 2>&1 || true
	docker network rm dh-polaris-net >/dev/null 2>&1 || true

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
