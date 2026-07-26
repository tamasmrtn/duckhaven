resource "azurerm_user_assigned_identity" "api" {
  name                = "id-duckhaven-api-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = local.tags
}

# The control plane: REST API, agent WebSocket endpoint, and the SPA.
#
# The only resource in this deployment with external ingress.
resource "azurerm_container_app" "api" {
  name                         = "api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = local.aca_workload_profile

  # Single revision. A new revision runs `alembic upgrade head` on start while the
  # previous revision is still serving, so every migration must be backward compatible
  # with the image it is replacing -- the same constraint the multi-replica compose
  # topology already imposes.
  revision_mode = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "postgres-password"
    key_vault_secret_id = azurerm_key_vault_secret.main["postgres-admin-password"].versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "api-secret-key"
    key_vault_secret_id = azurerm_key_vault_secret.main["api-secret-key"].versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "polaris-client-secret"
    key_vault_secret_id = azurerm_key_vault_secret.main["polaris-client-secret"].versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "acr-agent-pull-password"
    key_vault_secret_id = azurerm_key_vault_secret.acr_agent_pull_password.versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000

    # HTTP/1.1. The agent dial-home is a WebSocket upgrade, which http2 does not carry;
    # this is the transport the manual deployment validated WSS through.
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # Deliberately fixed rather than autoscaled. Agent WebSockets pin to a replica and
    # all query work is offloaded to agents, so scaling on HTTP concurrency would both
    # mismeasure load -- long-lived sockets inflate it indefinitely -- and churn socket
    # ownership. See api_min_replicas before raising this.
    min_replicas = var.api_min_replicas
    max_replicas = var.api_max_replicas

    container {
      name   = "api"
      image  = "${azurerm_container_registry.main.login_server}/${local.api_image_repository}:${var.duckhaven_image_tag}"
      cpu    = var.api_cpu
      memory = var.api_memory

      # ── Database ──
      # The entrypoint builds the asyncpg URL from these; DATABASE_URL is overwritten
      # unconditionally, so setting it here would have no effect.
      env {
        name  = "POSTGRES_HOST"
        value = azurerm_postgresql_flexible_server.main.fqdn
      }

      env {
        name  = "POSTGRES_PORT"
        value = "5432"
      }

      env {
        name  = "POSTGRES_USER"
        value = azurerm_postgresql_flexible_server.main.administrator_login
      }

      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "postgres-password"
      }

      env {
        name  = "POSTGRES_DB"
        value = azurerm_postgresql_flexible_server_database.duckhaven.name
      }

      # ── Sessions ──
      # Injected rather than left to the entrypoint's first-boot generation: /var/duckhaven
      # is ephemeral here, so a self-generated key would change on every replica
      # replacement and invalidate every session. A fixed key is also what lets replica
      # count grow later without cookies breaking.
      env {
        name        = "SECRET_KEY"
        secret_name = "api-secret-key"
      }

      env {
        name  = "COOKIE_SECURE"
        value = "true"
      }

      # The SPA is served from this same origin, so this only matters for external API
      # clients; the default points at a local dev server.
      env {
        name  = "CORS_ORIGINS"
        value = jsonencode(["https://${local.api_fqdn}"])
      }

      # ── Catalog ──
      env {
        name  = "POLARIS_BASE_URL"
        value = local.polaris_internal_url
      }

      env {
        name  = "POLARIS_REALM"
        value = var.polaris_realm
      }

      env {
        name  = "POLARIS_CLIENT_ID"
        value = local.polaris_client_id
      }

      env {
        name        = "POLARIS_CLIENT_SECRET"
        secret_name = "polaris-client-secret"
      }

      # ── Azure identity ──
      # Points DefaultAzureCredential at the user-assigned identity, for both the ADLS
      # user-delegation SAS the API mints itself and the ARM calls that create container
      # groups. Adding AZURE_TENANT_ID or AZURE_CLIENT_SECRET would divert the chain to
      # EnvironmentCredential and look for a secret that does not exist.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.api.client_id
      }

      # ── Elastic compute ──
      env {
        name  = "ELASTIC_COMPUTE_ENABLED"
        value = tostring(var.elastic_compute_enabled)
      }

      env {
        name  = "ELASTIC_PROVIDER"
        value = "azure_aci"
      }

      # Elastic provisioning has no HTTP request to derive this from, which is why it has
      # to be configured. Computed from the environment's domain rather than read back
      # off this app, which would be a self-reference.
      env {
        name  = "ELASTIC_CONTROL_PLANE_URL"
        value = "wss://${local.api_fqdn}/agents/connect"
      }

      # Agents reach Polaris over internal ingress, same as the API.
      env {
        name  = "ELASTIC_AGENT_POLARIS_BASE_URL"
        value = local.polaris_internal_url
      }

      env {
        name  = "ELASTIC_AZURE_SUBSCRIPTION_ID"
        value = var.subscription_id
      }

      env {
        name  = "ELASTIC_AZURE_RESOURCE_GROUP"
        value = azurerm_resource_group.agents.name
      }

      env {
        name  = "ELASTIC_AZURE_LOCATION"
        value = var.location
      }

      env {
        name  = "AGENT_IMAGE"
        value = "${azurerm_container_registry.main.login_server}/${local.agent_image_repository}:${var.duckhaven_image_tag}"
      }

      # Container instances cannot pull with a managed identity, so they get an explicit
      # credential: a repository-scoped token where the registry SKU allows one.
      env {
        name  = "ELASTIC_REGISTRY_SERVER"
        value = azurerm_container_registry.main.login_server
      }

      env {
        name  = "ELASTIC_REGISTRY_USERNAME"
        value = local.agent_pull_username
      }

      env {
        name        = "ELASTIC_REGISTRY_PASSWORD"
        secret_name = "acr-agent-pull-password"
      }

      # ── Observability ──
      # /api/metrics is unauthenticated and this app has public ingress, so the endpoint
      # stays off until scraping is wired through the VNet.
      env {
        name  = "METRICS_ENABLED"
        value = "false"
      }

      # The entrypoint runs `alembic upgrade head` before exec'ing uvicorn, so port 8000
      # is closed for the whole migration. A startup probe with a long budget is what
      # separates "still migrating" from "failed to start"; without it the liveness probe
      # would kill the container mid-migration and loop.
      startup_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/api/healthz"
        interval_seconds        = 10
        failure_count_threshold = 30
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/api/healthz"
        interval_seconds        = 10
        failure_count_threshold = 3
      }

      # /api/readyz, not /api/healthz: it exists for exactly this, and reports not-ready
      # on graceful shutdown so ingress drains a replica before it goes away.
      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/api/readyz"
        interval_seconds        = 10
        failure_count_threshold = 3
        success_count_threshold = 1
      }
    }
  }

  tags = local.tags

  depends_on = [
    # Migrations run on start, so the database and its schema target must exist.
    azurerm_postgresql_flexible_server_database.duckhaven,
    azurerm_role_assignment.api_key_vault_secrets,
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_storage_blob_data,
    azurerm_role_assignment.api_storage_blob_delegator,
  ]
}
