.PHONY: install install-web dev dev-api dev-web \
        test test-api test-agent test-web test-deploy test-shared \
        test-integration test-integration-api test-integration-agent \
        test-cross-component test-e2e \
        polaris-dev polaris-dev-s3 polaris-dev-down \
        localstack-dev localstack-dev-down \
        idp-dev idp-dev-down \
        lint format docs-index \
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
test: test-api test-agent test-web test-deploy test-shared

test-api:
	uv run --package duckhaven-api pytest api/tests/unit/ -n auto \
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
	uv run --package duckhaven-api pytest tests/deploy/ -v

test-shared:
	uv run --package duckhaven-api pytest shared/tests/unit/ -v

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
	cd tests/e2e && npm ci && npx playwright install --with-deps chromium && npx playwright test

# ── Local Polaris (for integration tests) ─────────────────────────────────────
# Spins up MinIO + Apache Polaris-on-S3 (in-memory persistence). Object storage
# is the only storage DuckHaven uses: Polaris vends scoped credentials so the
# agent's DuckDB can read AND write Iceberg tables. Override the bucket or image
# tag if needed. QUARKUS_OTEL_SDK_DISABLED stays true here (unlike the compose
# stacks): this dev flow runs no OTel collector, so exporting would only spam
# connection errors.
POLARIS_IMAGE_TAG ?= 1.7.0
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

# ── LocalStack (S3 + STS) for the external assume-role health/vending tests ───
# MinIO has no STS, so the external `s3` path (Polaris assumes an IAM role to
# vend creds) can only be exercised against LocalStack or real AWS. This brings
# up LocalStack and seeds a role + bucket; point `make polaris-dev` at it by
# setting POLARIS_S3_ENDPOINT(_INTERNAL) to the LocalStack URL, then run the
# tests with the printed DH_TEST_S3_* env. See docs/operations/storage-maintenance.md.
# Azure ADLS has no offline emulator for Entra credential vending (Azurite
# emulates Blob/SAS but not Entra), so the ADLS assume-identity path is
# validated against a real Azure account — the agent/api tests skip without it.
LOCALSTACK_BUCKET ?= dh-external-test
LOCALSTACK_ROLE ?= dh-polaris-role
# Pin a community image: localstack/localstack:latest now refuses to start
# without a (paid) license token. 3.8.x runs S3/STS/IAM free, no token.
LOCALSTACK_IMAGE_TAG ?= 3.8.1

localstack-dev:
	docker rm -f dh-localstack-dev >/dev/null 2>&1 || true
	docker run -d --name dh-localstack-dev -p 4566:4566 \
		-e SERVICES=s3,sts,iam localstack/localstack:$(LOCALSTACK_IMAGE_TAG)
	@echo "Waiting for LocalStack..."
	@for i in $$(seq 1 30); do \
		curl -sf http://localhost:4566/_localstack/health >/dev/null 2>&1 && break; sleep 1; \
	done
	docker run --rm --network host -e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test -e AWS_REGION=us-east-1 \
		amazon/aws-cli:2.34.48 --endpoint-url http://localhost:4566 \
		s3 mb s3://$(LOCALSTACK_BUCKET) || true
	docker run --rm --network host -e AWS_ACCESS_KEY_ID=test \
		-e AWS_SECRET_ACCESS_KEY=test -e AWS_REGION=us-east-1 \
		amazon/aws-cli:2.34.48 --endpoint-url http://localhost:4566 iam create-role \
		--role-name $(LOCALSTACK_ROLE) \
		--assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":"*"},"Action":"sts:AssumeRole"}]}' || true
	@echo ""
	@echo "LocalStack S3+STS ready. Run the external assume-role tests with:"
	@echo "  DH_TEST_S3_ROLE_ARN=arn:aws:iam::000000000000:role/$(LOCALSTACK_ROLE) \\"
	@echo "  DH_TEST_S3_ROOT_URI=s3://$(LOCALSTACK_BUCKET)/duckhaven \\"
	@echo "  DH_TEST_S3_ENDPOINT=http://localhost:4566 make test-integration"

localstack-dev-down:
	docker rm -f dh-localstack-dev >/dev/null 2>&1 || true
	docker network rm dh-polaris-net >/dev/null 2>&1 || true

# ── Local IdP + LDAP (for SSO/LDAP integration tests) ─────────────────────────
# Brings up Keycloak (realm imported from deploy/keycloak) and OpenLDAP (seeded
# from deploy/openldap/bootstrap, with the memberof overlay). Both are env-gated
# in the test suite, so integration tests skip cleanly when these aren't up.
KEYCLOAK_IMAGE_TAG ?= 26.0
OPENLDAP_IMAGE_TAG ?= 1.5.0

idp-dev:
	docker rm -f dh-keycloak-dev dh-openldap-dev >/dev/null 2>&1 || true
	docker run -d --name dh-keycloak-dev -p 8080:8080 \
		-e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin \
		-v $(PWD)/deploy/keycloak/duckhaven-realm.json:/opt/keycloak/data/import/duckhaven-realm.json:ro \
		quay.io/keycloak/keycloak:$(KEYCLOAK_IMAGE_TAG) start-dev --import-realm
	docker run -d --name dh-openldap-dev -p 389:389 \
		-e LDAP_ORGANISATION="DuckHaven" -e LDAP_DOMAIN="duckhaven.test" \
		-e LDAP_ADMIN_PASSWORD="admin" \
		-v $(PWD)/deploy/openldap/bootstrap:/container/service/slapd/assets/config/bootstrap/ldif/custom:ro \
		osixia/openldap:$(OPENLDAP_IMAGE_TAG) --copy-service
	@echo "Waiting for Keycloak..."
	@for i in $$(seq 1 60); do \
		curl -sf http://localhost:8080/realms/duckhaven/.well-known/openid-configuration >/dev/null 2>&1 \
			&& { echo "Keycloak is up."; break; }; sleep 2; \
	done
	@echo ""
	@echo "Keycloak + OpenLDAP ready. Run SSO/LDAP integration tests with:"
	@echo "  OIDC_SERVER_METADATA_URL=http://localhost:8080/realms/duckhaven/.well-known/openid-configuration \\"
	@echo "  OIDC_CLIENT_ID=duckhaven-api OIDC_CLIENT_SECRET=duckhaven-secret \\"
	@echo "  LDAP_SERVER_URI=ldap://localhost:389 \\"
	@echo "  LDAP_BIND_DN='cn=admin,dc=duckhaven,dc=test' LDAP_BIND_PASSWORD=admin \\"
	@echo "  LDAP_USER_SEARCH_BASE='ou=people,dc=duckhaven,dc=test' \\"
	@echo "  make test-integration-api"

idp-dev-down:
	docker rm -f dh-keycloak-dev dh-openldap-dev >/dev/null 2>&1 || true

# ── Lint / Format ─────────────────────────────────────────────────────────────
lint:
	uv run ruff check api/src agent/src shared/src
	cd web && npm run lint

format:
	uv run ruff format api/src agent/src shared/src
	cd web && npm run format

# ── Assistant documentation index ─────────────────────────────────────────────
# Regenerates docs_index.yaml (what the assistant sees) and docs/llms.txt from
# docs/ and mkdocs.yml. Hand-edited page summaries are preserved. Run after
# adding, removing or renaming a docs page; pre-commit and CI check for drift.
docs-index:
	uv run --package duckhaven-api python -m api.services.assistant.knowledge.generate

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
#
# Run from `deploy/` rather than pointing `-f` at the file from here. Compose
# only looks for `docker-compose.override.yml` when it is discovering files on
# its own, and naming one with `-f` turns that off — so the override was being
# ignored, silently, by every target below. That is how a stack comes up without
# the settings its own override file turns on (SQL sessions, say, which the dbt
# adapter needs) and reports them as simply unavailable.
#
# The project name is derived from the directory either way, so this keeps
# addressing the same `deploy-*` containers as before.
compose-up:
	cd deploy && docker compose up -d

compose-down:
	cd deploy && docker compose down

compose-logs:
	cd deploy && docker compose logs -f

compose-pull:
	cd deploy && docker compose pull

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf htmlcov/ .coverage .coverage.*
	rm -rf web/dist web/node_modules
