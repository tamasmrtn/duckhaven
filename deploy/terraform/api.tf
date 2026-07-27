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

  # No postgres-password secret: the API authenticates to Postgres with its managed
  # identity, so there is no database credential to hold. Polaris and the two
  # bootstrap jobs are the only things left that need one.

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
    name                = "internal-api-secret"
    key_vault_secret_id = azurerm_key_vault_secret.main["internal-api-secret"].versionless_id
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
      # Passwordless. The URL carries the user and host only; asyncpg is handed a
      # freshly minted Entra access token as the password on every connection (see
      # api/src/api/db/entra.py), which is why nothing here is a secret and why the
      # entrypoint must not rebuild this from POSTGRES_* -- it would have to
      # interpolate a password that does not exist.
      #
      # The user is the API identity's *name*: that is what the login role created by
      # the db-bootstrap job is called.
      env {
        name = "DATABASE_URL"
        value = join("", [
          "postgresql+asyncpg://",
          azurerm_user_assigned_identity.api.name,
          "@",
          azurerm_postgresql_flexible_server.main.fqdn,
          ":5432/",
          azurerm_postgresql_flexible_server_database.duckhaven.name,
          "?ssl=require",
        ])
      }

      env {
        name  = "DB_AUTH_MODE"
        value = "entra"
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

      # ── Replica identity ──
      # Container Apps gives every replica identical configuration and no
      # individually addressable hostname, so each one derives its own: "auto" makes it
      # read the platform's replica name and its own container address. A static value
      # would have every replica claim the same owner_url on an agent row, and
      # cross-replica dispatch would give up rather than forward.
      env {
        name  = "REPLICA_ID"
        value = "auto"
      }

      env {
        name  = "REPLICA_INTERNAL_URL"
        value = "auto"
      }

      # Guards the /internal forwarding endpoints, which are reachable on the replica
      # address above. Without it peer forwarding stays disabled and an agent held by
      # another replica is treated as unreachable.
      env {
        name        = "INTERNAL_API_SECRET"
        secret_name = "internal-api-secret"
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
        value = local.polaris_url
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

      # Raised from the 10s default after watching a first storage-backend health check
      # time out at 10s while Polaris was still working: its first write to a new
      # account spends several seconds acquiring a managed-identity token and loading
      # the ADLS FileIO implementation, and the commit alone took 12s. Later calls are
      # fast because the token is cached, but the cold path has to fit inside this.
      env {
        name  = "POLARIS_HTTP_TIMEOUT_S"
        value = "45"
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
        value = local.polaris_url
      }

      env {
        name  = "ELASTIC_AZURE_SUBSCRIPTION_ID"
        value = data.azurerm_client_config.current.subscription_id
      }

      env {
        name  = "ELASTIC_AZURE_RESOURCE_GROUP"
        value = azurerm_resource_group.agents.name
      }

      env {
        name  = "ELASTIC_AZURE_LOCATION"
        value = var.location
      }

      # Delegated subnet the agent container groups are injected into. This is what
      # gives each agent a private address instead of a public one, so its result
      # server is reachable only from this virtual network.
      env {
        name  = "ELASTIC_AZURE_SUBNET_ID"
        value = azurerm_subnet.aci.id
      }

      env {
        name  = "AGENT_IMAGE"
        value = "${azurerm_container_registry.main.login_server}/${local.agent_image_repository}:${var.duckhaven_image_tag}"
      }

      # How a provisioned container group pulls the agent image: the control plane
      # attaches this identity to the group, and the group authenticates to the
      # registry as itself. No registry password exists to be passed here or to sit in
      # the group's spec.
      env {
        name  = "ELASTIC_REGISTRY_SERVER"
        value = azurerm_container_registry.main.login_server
      }

      env {
        name  = "ELASTIC_REGISTRY_IDENTITY_ID"
        value = azurerm_user_assigned_identity.agent.id
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
    # The API cannot log in until db-bootstrap has created its Entra role. Terraform
    # can only guarantee the job exists, not that it has been run -- until it has,
    # replicas fail their startup probe. See the deployment README.
    azurerm_container_app_job.db_bootstrap,
    azurerm_role_assignment.api_key_vault_secrets,
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_storage_blob_data,
    azurerm_role_assignment.api_storage_blob_delegator,
  ]
}
