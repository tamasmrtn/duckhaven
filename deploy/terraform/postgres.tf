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
    # Password auth stays on because deploy/api-entrypoint.sh builds a
    # postgresql+asyncpg:// URL and has no Entra-token path. Entra auth is enabled
    # alongside it purely for human administrators.
    password_auth_enabled         = true
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
