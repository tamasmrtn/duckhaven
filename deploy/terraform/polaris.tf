resource "azurerm_user_assigned_identity" "polaris" {
  name                = "id-duckhaven-polaris-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = local.tags
}

# ── Bootstrap ─────────────────────────────────────────────────────────────────

# Creates the realm and root principal in the polaris database. A manual-trigger job
# rather than part of the app: it must run exactly once against a given database, before
# the server starts, and it must be re-runnable without breaking anything.
#
# The manual deployment ran this as a one-shot container instance because Container Apps
# Jobs could not reach its Postgres. That was specific to a Consumption environment
# talking to a public endpoint -- a job in this VNet-injected environment resolves the
# private endpoint and connects, which was verified directly during the Phase 0 spike.
resource "azurerm_container_app_job" "polaris_bootstrap" {
  name                         = "polaris-bootstrap"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = local.aca_workload_profile

  replica_timeout_in_seconds = 300

  # No retries: the interesting failures here are a wrong password or an unreachable
  # database, neither of which a retry fixes, and the wrapper below already treats an
  # already-bootstrapped realm as success.
  replica_retry_limit = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.polaris.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.polaris.id
  }

  secret {
    name                = "postgres-password"
    key_vault_secret_id = azurerm_key_vault_secret.main["postgres-admin-password"].versionless_id
    identity            = azurerm_user_assigned_identity.polaris.id
  }

  secret {
    name                = "polaris-client-secret"
    key_vault_secret_id = azurerm_key_vault_secret.main["polaris-client-secret"].versionless_id
    identity            = azurerm_user_assigned_identity.polaris.id
  }

  template {
    container {
      name   = "bootstrap"
      image  = local.polaris_admin_tool_image
      cpu    = 0.5
      memory = "1Gi"

      # Same contract as deploy/polaris-bootstrap.sh, inlined because a job cannot
      # mount a script: the admin tool exits 3 when the realm already exists, which is
      # success for our purposes. Inlining keeps the two definitions visibly parallel.
      command = ["/bin/sh", "-c"]
      args = [
        join("\n", [
          "java -jar /deployments/polaris-admin-tool.jar bootstrap \\",
          "  --realm=\"$POLARIS_REALM\" \\",
          "  --credential=\"$POLARIS_REALM,$POLARIS_CLIENT_ID,$POLARIS_CLIENT_SECRET\"",
          "status=$?",
          "if [ \"$status\" -eq 0 ] || [ \"$status\" -eq 3 ]; then exit 0; fi",
          "exit \"$status\"",
        ])
      ]

      env {
        name  = "POLARIS_PERSISTENCE_TYPE"
        value = "relational-jdbc"
      }

      env {
        name  = "POLARIS_PERSISTENCE_RELATIONAL_JDBC_DATABASE_TYPE"
        value = "postgresql"
      }

      env {
        name  = "QUARKUS_DATASOURCE_JDBC_URL"
        value = local.polaris_jdbc_url
      }

      env {
        name  = "QUARKUS_DATASOURCE_USERNAME"
        value = azurerm_postgresql_flexible_server.main.administrator_login
      }

      env {
        name        = "QUARKUS_DATASOURCE_PASSWORD"
        secret_name = "postgres-password"
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
    }
  }

  tags = local.tags

  depends_on = [
    azurerm_role_assignment.polaris_key_vault_secrets,
    azurerm_role_assignment.polaris_acr_pull,
  ]
}

# ── Server ────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "polaris" {
  name                         = "polaris"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = local.aca_workload_profile

  # Single revision mode: there is no scenario where two Polaris versions should serve
  # the same catalog database at once.
  revision_mode = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.polaris.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.polaris.id
  }

  secret {
    name                = "postgres-password"
    key_vault_secret_id = azurerm_key_vault_secret.main["postgres-admin-password"].versionless_id
    identity            = azurerm_user_assigned_identity.polaris.id
  }

  secret {
    name                = "polaris-client-secret"
    key_vault_secret_id = azurerm_key_vault_secret.main["polaris-client-secret"].versionless_id
    identity            = azurerm_user_assigned_identity.polaris.id
  }

  # Internal ingress: reachable from the Container Apps subnet and from VNet-injected
  # agents, and from nowhere on the internet. This is the difference from the manual
  # deployment, which exposed Polaris publicly because its agents had public IPs.
  ingress {
    external_enabled           = false
    target_port                = 8181
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.polaris_min_replicas
    max_replicas = var.polaris_max_replicas

    container {
      name   = "polaris"
      image  = local.polaris_image
      cpu    = var.polaris_cpu
      memory = var.polaris_memory

      env {
        name  = "POLARIS_PERSISTENCE_TYPE"
        value = "relational-jdbc"
      }

      env {
        name  = "POLARIS_PERSISTENCE_RELATIONAL_JDBC_DATABASE_TYPE"
        value = "postgresql"
      }

      env {
        name  = "QUARKUS_DATASOURCE_JDBC_URL"
        value = local.polaris_jdbc_url
      }

      env {
        name  = "QUARKUS_DATASOURCE_USERNAME"
        value = azurerm_postgresql_flexible_server.main.administrator_login
      }

      env {
        name        = "QUARKUS_DATASOURCE_PASSWORD"
        secret_name = "postgres-password"
      }

      env {
        name  = "POLARIS_REALM_CONTEXT_REALMS"
        value = var.polaris_realm
      }

      # Quarkus maps this to polaris.readiness.ignore-severe-issues. The dotted property
      # name itself is not a legal environment variable, so the mangled form is the only
      # way to set it here.
      env {
        name  = "POLARIS_READINESS_IGNORE_SEVERE_ISSUES"
        value = "true"
      }

      # Only AZURE. The bundled MinIO/S3 path does not exist in this deployment, so no
      # AWS credentials and no ALLOW_INSECURE_STORAGE_TYPES.
      #
      # This one cannot go through an environment variable at all: the property key
      # contains dots and quotes (polaris.features."SUPPORTED_CATALOG_STORAGE_TYPES"),
      # which Kubernetes -- and therefore Container Apps -- rejects as a variable name,
      # and Quarkus's uppercase mangling cannot reproduce the quoting. Passing it as a
      # JVM system property is the documented way round it.
      env {
        name  = "JAVA_OPTS_APPEND"
        value = "-Dpolaris.features.\"SUPPORTED_CATALOG_STORAGE_TYPES\"=[\"AZURE\"]"
      }

      # Polaris mints the ADLS SAS it vends to agents using DefaultAzureCredential.
      # Pointing that at a user-assigned identity requires the client id and nothing
      # else -- adding AZURE_TENANT_ID or AZURE_CLIENT_SECRET would divert the chain to
      # EnvironmentCredential and look for a secret that does not exist. This is what
      # replaces the manual deployment's shared service principal.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.polaris.client_id
      }

      # Management endpoint, on its own port. 8181 serves the catalog REST API.
      liveness_probe {
        transport = "HTTP"
        port      = 8182
        path      = "/q/health"

        # A JVM start is not instant, and killing it mid-boot produces a crash loop
        # that looks like a configuration fault.
        initial_delay           = 30
        interval_seconds        = 10
        failure_count_threshold = 5
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8182
        path                    = "/q/health"
        interval_seconds        = 10
        failure_count_threshold = 5
        success_count_threshold = 1
      }
    }
  }

  tags = local.tags

  depends_on = [
    # The schema must exist before the server connects to it.
    azurerm_container_app_job.polaris_bootstrap,
    azurerm_role_assignment.polaris_key_vault_secrets,
    azurerm_role_assignment.polaris_acr_pull,
    azurerm_role_assignment.polaris_storage_blob_data,
    azurerm_role_assignment.polaris_storage_blob_delegator,
  ]
}
