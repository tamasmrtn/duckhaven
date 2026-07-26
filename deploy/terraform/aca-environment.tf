# The environment both DuckHaven apps run in.
#
# Per-app ingress scope is why this design needs no Application Gateway: the API can
# take external ingress while Polaris stays internal-only inside the same environment.
resource "azurerm_container_app_environment" "main" {
  name                = "cae-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location

  # Bound at creation, not attached afterwards. The manual deployment created its
  # environment with logs disabled and added a workspace later, which meant the logs
  # needed to debug the bring-up did not exist while it was being debugged.
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  # VNet injection. Required for zone redundancy, and it is what puts replicas on a
  # network that can reach the private endpoints and the agent subnet.
  infrastructure_subnet_id = azurerm_subnet.aca.id

  # False so individual apps choose their own ingress scope. An internal-only
  # environment would force the API behind a separate public entry point.
  internal_load_balancer_enabled = false

  zone_redundancy_enabled = true

  # Consumption bills per vCPU-second and GiB-second of running replicas with no
  # standing charge for the profile, unlike a dedicated profile which bills for
  # instances whether or not anything runs on them.
  workload_profile {
    name                  = local.aca_workload_profile
    workload_profile_type = local.aca_workload_profile
  }

  # Named explicitly; the platform otherwise invents ME_<env>_<rg>_<region>.
  infrastructure_resource_group_name = "rg-${local.name}-aca"

  tags = local.tags
}
