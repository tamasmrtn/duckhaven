output "api_url" {
  description = "The only public endpoint in this deployment: the DuckHaven UI and REST API."
  value       = module.duckhaven.api_url
}

output "resource_group_name" {
  description = "Main resource group; the two bootstrap jobs are started against it."
  value       = module.duckhaven.resource_group_name
}

output "container_registry_login_server" {
  description = "Registry hostname. Push the API and agent images here before the apps are created."
  value       = module.duckhaven.container_registry_login_server
}

output "warehouse_root_uri" {
  description = "Root URI to register as an adls_gen2 storage backend (hierarchical = true)."
  value       = module.duckhaven.warehouse_root_uri
}

output "setup_token" {
  description = "One-time token gating first-admin creation. Spend it via next_steps."
  value       = module.duckhaven.setup_token
  sensitive   = true
}

output "next_steps" {
  description = "The commands to run after this apply, with every name already filled in."
  value       = module.duckhaven.next_steps
}
