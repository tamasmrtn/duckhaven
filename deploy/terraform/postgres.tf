# Managed PostgreSQL, replacing the container that the manual deployment ran Postgres
# in. One server hosts both databases DuckHaven needs, so the custom postgres image
# whose only job was `CREATE DATABASE polaris` disappears.
resource "azurerm_postgresql_flexible_server" "main" {
  name                = "psql-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  version             = "17"
  sku_name            = var.postgres_sku_name
  storage_mb          = var.postgres_storage_mb
  storage_tier        = var.postgres_storage_tier

  administrator_login    = "dhadmin"
  administrator_password = random_password.postgres_admin.result

  backup_retention_days        = var.postgres_backup_retention_days
  geo_redundant_backup_enabled = var.postgres_geo_redundant_backup_enabled

  # Reached exclusively through the private endpoint below. Setting this false is
  # what makes the server refuse its public FQDN; it is mutually exclusive with the
  # delegated-subnet (VNet injection) model, which this deployment does not use --
  # a private endpoint keeps the server out of the VNet address space and lets the
  # same pattern serve every other private service here.
  public_network_access_enabled = false

  zone = var.postgres_zone

  dynamic "high_availability" {
    for_each = var.postgres_high_availability_enabled ? [1] : []

    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = "2"
    }
  }

  authentication {
    # Both, by default. The API authenticates with a managed identity and needs no
    # password at all, but Polaris cannot: its Quarkus datasource would need
    # azure-identity-extensions on the pgjdbc classpath, which the stock
    # apache/polaris image does not ship. So password auth stays on for Polaris --
    # and can be switched off entirely by a deployment that does not run it.
    password_auth_enabled         = var.postgres_password_auth_enabled
    active_directory_auth_enabled = true
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  maintenance_window {
    day_of_week  = 0 # Sunday
    start_hour   = 2
    start_minute = 0
  }

  tags = local.tags

  lifecycle {
    # Azure reassigns these during a zone failover, which would otherwise show up as
    # a permanent diff and, worse, tempt a "fix" that recreates the server.
    ignore_changes = [zone, high_availability[0].standby_availability_zone]
  }
}

# DuckHaven's own schema, managed by Alembic on API container start.
resource "azurerm_postgresql_flexible_server_database" "duckhaven" {
  name      = "duckhaven"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Polaris keeps its own relational-jdbc schema here. Separate database, same server:
# the schemas never collide and there is one thing to back up and fail over.
resource "azurerm_postgresql_flexible_server_database" "polaris" {
  name      = "polaris"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "ops" {
  count = var.postgres_entra_admin == null ? 0 : 1

  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = var.postgres_entra_admin.object_id
  principal_name      = var.postgres_entra_admin.principal_name
  principal_type      = var.postgres_entra_admin.principal_type
}

# ── Entra login roles ─────────────────────────────────────────────────────────

# An identity that exists only to create login roles for the workload identities.
#
# An Entra principal can authenticate to the server but cannot log in until a
# database role exists for it, and creating one means running
# pgaadauth_create_principal as an Entra administrator. Registering the *API's* own
# identity as that administrator would skip this whole file, at the cost of giving
# the internet-facing app azure_pg_admin over every database on the server. This
# identity holds that instead, is used exactly once by the job below, and is never
# attached to a running app.
resource "azurerm_user_assigned_identity" "db_bootstrap" {
  name                = "id-duckhaven-dbadmin-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  tags                = local.tags
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "db_bootstrap" {
  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = azurerm_user_assigned_identity.db_bootstrap.principal_id
  principal_name      = azurerm_user_assigned_identity.db_bootstrap.name
  principal_type      = "ServicePrincipal"
}

# Runs api.tools.db_bootstrap against the private endpoint. A job rather than part
# of the apply because the server has no public endpoint: a Terraform runner outside
# the VNet cannot reach it, while a job in this environment resolves privatelink and
# connects -- the same reasoning, and the same shape, as polaris_bootstrap.
#
# Re-runnable: the tool skips a role that already exists and the grants are idempotent.
resource "azurerm_container_app_job" "db_bootstrap" {
  name                         = "db-bootstrap"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = local.aca_workload_profile

  replica_timeout_in_seconds = 300
  replica_retry_limit        = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.db_bootstrap.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.db_bootstrap.id
  }

  template {
    container {
      name   = "db-bootstrap"
      image  = "${azurerm_container_registry.main.login_server}/${local.api_image_repository}:${var.duckhaven_image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      # Bypasses the image's entrypoint, which would run alembic first -- and
      # migrations are exactly what cannot work until this job has run.
      command = ["python", "-m", "api.tools.db_bootstrap"]

      env {
        name  = "DB_BOOTSTRAP_HOST"
        value = azurerm_postgresql_flexible_server.main.fqdn
      }

      # The role name for an Entra login is the identity's own name.
      env {
        name  = "DB_BOOTSTRAP_USER"
        value = azurerm_user_assigned_identity.db_bootstrap.name
      }

      # Only the API. Polaris authenticates with a password because its Quarkus
      # datasource has no Entra path, so it needs no login role here.
      env {
        name  = "DB_BOOTSTRAP_PRINCIPALS"
        value = azurerm_user_assigned_identity.api.name
      }

      env {
        name  = "DB_BOOTSTRAP_DATABASES"
        value = azurerm_postgresql_flexible_server_database.duckhaven.name
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.db_bootstrap.client_id
      }
    }
  }

  tags = local.tags

  depends_on = [
    azurerm_postgresql_flexible_server_active_directory_administrator.db_bootstrap,
    azurerm_postgresql_flexible_server_database.duckhaven,
    azurerm_role_assignment.db_bootstrap_acr_pull,
  ]
}

module "pe_postgres" {
  source = "./modules/private-endpoint"

  name                 = "pe-psql-${local.name}"
  location             = var.location
  resource_group_name  = azurerm_resource_group.main.name
  subnet_id            = azurerm_subnet.pe.id
  target_resource_id   = azurerm_postgresql_flexible_server.main.id
  subresource_names    = ["postgresqlServer"]
  private_dns_zone_ids = [azurerm_private_dns_zone.main["postgres"].id]
  tags                 = local.tags
}
