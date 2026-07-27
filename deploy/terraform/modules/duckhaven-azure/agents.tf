# Permissions the control plane needs to manage elastic agent container groups.
#
# The resource group itself is in main.tf, deliberately separate from everything else:
# api/src/api/services/compute/reaper.py reconciles that group against DuckHaven's
# records and terminates every container group in it tagged duckhaven-managed=true with
# no live agent row. Anything else placed there would eventually be deleted.

data "azurerm_subscription" "current" {}

# Narrower than Contributor, which is what the manual deployment used. The control plane
# needs to create, inspect and delete container groups, and to place them in a subnet --
# nothing else.
resource "azurerm_role_definition" "elastic_agents" {
  # Scoped per environment: role definition names must be unique, so a shared
  # subscription hosting both prod and staging would otherwise collide.
  name        = "DuckHaven Elastic Agents (${var.environment})"
  scope       = data.azurerm_subscription.current.id
  description = "Create, read and delete DuckHaven elastic agent container groups, and join them to the agent subnet."

  permissions {
    actions = [
      "Microsoft.ContainerInstance/containerGroups/read",
      "Microsoft.ContainerInstance/containerGroups/write",
      "Microsoft.ContainerInstance/containerGroups/delete",

      # Subnet injection: creating a container group with a private address requires
      # permission to join the subnet, and to read it. Needed from phase 5 onwards,
      # granted now so the permission is not the thing that blocks that change.
      "Microsoft.Network/virtualNetworks/subnets/read",
      "Microsoft.Network/virtualNetworks/subnets/join/action",

      # Attaching the agent identity to a container group. Creating a resource that
      # *carries* an identity requires this on the identity itself, separately from any
      # permission the identity holds -- without it provisioning fails authorization
      # before it ever reaches the image pull, which reads as a puzzling error.
      "Microsoft.ManagedIdentity/userAssignedIdentities/assign/action",
    ]
    not_actions = []
  }

  # Every scope the role is assigned at, below. The VNet covers assignment at the
  # subnet, being its parent.
  assignable_scopes = [
    azurerm_resource_group.agents.id,
    azurerm_virtual_network.main.id,
    azurerm_user_assigned_identity.agent.id,
  ]
}

# Two assignments rather than one at a common ancestor. Each grants the role's full
# action set, but only the container-group actions mean anything at a resource group
# holding no networks, and only the subnet actions mean anything at a subnet holding no
# container groups -- so the effective permission at each scope stays minimal, and
# neither reaches the rest of the subscription.
resource "azurerm_role_assignment" "api_elastic_agents_rg" {
  scope              = azurerm_resource_group.agents.id
  role_definition_id = azurerm_role_definition.elastic_agents.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_role_assignment" "api_elastic_agents_subnet" {
  scope              = azurerm_subnet.aci.id
  role_definition_id = azurerm_role_definition.elastic_agents.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.api.principal_id
}

# Only the assign action means anything at an identity, so this grants the control
# plane exactly one thing: the ability to hand this identity to a container group it
# creates. It does not let the API act *as* the identity.
resource "azurerm_role_assignment" "api_elastic_agents_identity" {
  scope              = azurerm_user_assigned_identity.agent.id
  role_definition_id = azurerm_role_definition.elastic_agents.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.api.principal_id
}
