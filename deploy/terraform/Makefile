# Wraps the ordered first-deployment sequence, which is otherwise several pages of
# README an operator has to translate into commands.
#
#   make init ROOT=examples/quickstart BACKEND=backend.hcl
#   make registry ROOT=examples/quickstart
#   make images ROOT=examples/quickstart TAG=v1.0.0
#   make apply ROOT=examples/quickstart
#   make bootstrap ROOT=examples/quickstart
#
# ROOT selects which example to operate on. VARS passes a -var-file, which
# examples/production needs and examples/quickstart does not.

ROOT    ?= examples/quickstart
BACKEND ?= backend.hcl
VARS    ?=
TF       = terraform -chdir=$(ROOT)
TFVARS   = $(if $(VARS),-var-file=$(VARS),)

.PHONY: help init registry images apply bootstrap outputs destroy fmt validate

help:
	@echo "ROOT=$(ROOT)"
	@echo
	@echo "  make init       - terraform init against BACKEND=$(BACKEND)"
	@echo "  make registry   - first apply, registry only (see 'the first apply is staged')"
	@echo "  make images     - build and push the API and agent images at TAG"
	@echo "  make apply      - full apply"
	@echo "  make bootstrap  - run the database and Polaris bootstrap jobs"
	@echo "  make outputs    - print the next-steps commands"
	@echo "  make destroy    - tear the environment down"

init:
	$(TF) init -backend-config=$(BACKEND)

# A Container App whose image cannot be pulled never becomes healthy, and the images
# can only be pushed once the registry exists. -target is a documented troubleshooting
# tool rather than a workflow, and this is the one place it earns its keep: there is no
# way to express "create this resource first" across an apply boundary.
registry:
	$(TF) apply $(TFVARS) -target=module.duckhaven.azurerm_container_registry.main

images:
	@test -n "$(TAG)" || { echo "TAG is required, e.g. make images TAG=v1.0.0"; exit 2; }
	$(eval ACR := $(shell $(TF) output -raw container_registry_login_server))
	az acr login -n "$(ACR)"
	cd ../.. && docker build --platform linux/amd64 -f api/Dockerfile   -t "$(ACR)/duckhaven-api:$(TAG)"   .
	cd ../.. && docker build --platform linux/amd64 -f agent/Dockerfile -t "$(ACR)/duckhaven-agent:$(TAG)" .
	docker push "$(ACR)/duckhaven-api:$(TAG)"
	docker push "$(ACR)/duckhaven-agent:$(TAG)"

apply:
	$(TF) apply $(TFVARS)

# Both jobs are manual-trigger and both are safe to re-run: db-bootstrap skips a role
# that already exists, and the Polaris admin tool exits 3 on an existing realm, which
# the job's command treats as success.
bootstrap:
	$(eval RG := $(shell $(TF) output -raw resource_group_name))
	az containerapp job start -n db-bootstrap -g "$(RG)"
	az containerapp job start -n polaris-bootstrap -g "$(RG)"

outputs:
	@$(TF) output -raw next_steps

destroy:
	$(TF) destroy $(TFVARS)

fmt:
	terraform fmt -recursive

validate:
	$(TF) validate
