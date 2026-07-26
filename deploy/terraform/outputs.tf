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

output "nat_gateway_public_ip" {
  description = "Egress IP for the Container Apps and agent subnets. Allowlist this on external services."
  value       = azurerm_public_ip.natgw.ip_address
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

output "private_dns_zone_ids" {
  description = "Private DNS zone IDs, keyed by service."
  value       = { for k, z in azurerm_private_dns_zone.main : k => z.id }
}
