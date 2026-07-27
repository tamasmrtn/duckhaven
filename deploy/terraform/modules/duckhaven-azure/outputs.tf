output "resource_group_name" {
  description = "Main resource group."
  value       = azurerm_resource_group.main.name
}

output "agents_resource_group_name" {
  description = "Dedicated resource group for elastic agent container groups (ELASTIC_AZURE_RESOURCE_GROUP)."
  value       = azurerm_resource_group.agents.name
}

output "virtual_network_id" {
  description = "VNet resource ID."
  value       = azurerm_virtual_network.main.id
}

output "agent_subnet_id" {
  description = "Subnet elastic agents are injected into (ELASTIC_AZURE_SUBNET_ID)."
  value       = azurerm_subnet.aci.id
}

output "container_apps_subnet_id" {
  description = "Container Apps infrastructure subnet."
  value       = azurerm_subnet.aca.id
}

output "private_endpoint_subnet_id" {
  description = "Subnet holding all private endpoint NICs."
  value       = azurerm_subnet.pe.id
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace resource ID."
  value       = azurerm_log_analytics_workspace.main.id
}

output "key_vault_id" {
  description = "Key Vault resource ID."
  value       = azurerm_key_vault.main.id
}

output "key_vault_uri" {
  description = "Key Vault URI."
  value       = azurerm_key_vault.main.vault_uri
}

output "postgres_fqdn" {
  description = <<-EOT
    Postgres host. When this stack creates the server it resolves to a private endpoint
    address inside the VNet; otherwise it echoes postgres_existing_server_fqdn.
  EOT
  value       = local.postgres_fqdn
}

output "postgres_administrator_login" {
  description = <<-EOT
    Password-auth admin user, which exists for Polaris only -- the API connects with
    its managed identity. The password is in Key Vault. Null when this stack does not
    create the server.
  EOT
  value       = one(azurerm_postgresql_flexible_server.main[*].administrator_login)
}

output "storage_account_name" {
  description = "ADLS Gen2 account holding the Iceberg warehouse."
  value       = azurerm_storage_account.warehouse.name
}

output "warehouse_root_uri" {
  description = "Root URI to register as an adls_gen2 storage backend (hierarchical = true)."
  value       = "abfss://${azurerm_storage_data_lake_gen2_filesystem.warehouse.name}@${azurerm_storage_account.warehouse.name}.dfs.core.windows.net/duckhaven/"
}

output "container_registry_login_server" {
  description = "Registry hostname (ELASTIC_REGISTRY_SERVER)."
  value       = azurerm_container_registry.main.login_server
}

output "agent_identity_id" {
  description = <<-EOT
    Identity provisioned agent container groups carry and pull their image as
    (ELASTIC_REGISTRY_IDENTITY_ID). Holds AcrPull on the registry and nothing else.
  EOT
  value       = azurerm_user_assigned_identity.agent.id
}

output "nat_gateway_public_ip" {
  description = <<-EOT
    Egress IP for the Container Apps and agent subnets; null when the NAT gateway is
    disabled, in which case elastic agents have no outbound route.
  EOT
  value       = var.nat_gateway_enabled ? azurerm_public_ip.natgw[0].ip_address : null
}

output "api_url" {
  description = "The only public endpoint in this deployment: the DuckHaven UI and REST API."
  value       = "https://${local.api_fqdn}"
}

output "agent_control_plane_url" {
  description = "WebSocket URL provisioned agents dial home to (ELASTIC_CONTROL_PLANE_URL)."
  value       = "wss://${local.api_fqdn}/agents/connect"
}

output "container_app_environment_default_domain" {
  description = <<-EOT
    Domain the environment issues app hostnames under. Readable before any app exists,
    which is what lets the API's own FQDN be computed without a dependency cycle on
    ELASTIC_CONTROL_PLANE_URL.
  EOT
  value       = azurerm_container_app_environment.main.default_domain
}

output "polaris_url" {
  description = <<-EOT
    Polaris catalog REST endpoint (POLARIS_BASE_URL and ELASTIC_AGENT_POLARIS_BASE_URL).
    Internal to the Container Apps environment unless elastic compute is enabled, in
    which case it is a public listener restricted to this deployment's egress address.
  EOT
  value       = local.polaris_url
}

output "private_dns_zone_ids" {
  description = "Private DNS zone IDs, keyed by service."
  value       = { for k, z in azurerm_private_dns_zone.main : k => z.id }
}
