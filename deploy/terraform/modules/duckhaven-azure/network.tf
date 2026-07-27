resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = [var.vnet_address_space]
  tags                = local.tags
}

# ── Subnets ───────────────────────────────────────────────────────────────────

resource "azurerm_subnet" "aca" {
  name                 = "snet-aca"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [var.subnet_prefix_aca]

  delegation {
    name = "aca-environment"

    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "pe" {
  name                 = "snet-pe"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [var.subnet_prefix_pe]

  # Private endpoint NICs bypass NSGs and UDRs unless these policies are enabled.
  # They stay disabled, which is why no NSG is attached to this subnet: it would be
  # a no-op that implied protection it does not provide.
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_subnet" "aci" {
  name                 = "snet-aci"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = [var.subnet_prefix_aci]

  delegation {
    name = "aci-container-groups"

    service_delegation {
      name    = "Microsoft.ContainerInstance/containerGroups"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

# ── Outbound ──────────────────────────────────────────────────────────────────

# Azure is retiring default outbound access, so the agent subnet needs an explicit
# egress path. Attaching the same gateway to the Container Apps subnet gives the
# whole deployment one predictable egress IP.
# Both the gateway and its public IP bill hourly whether or not traffic flows, which is
# why they can be switched off for an environment that is not exercising elastic agents.
resource "azurerm_public_ip" "natgw" {
  count = var.nat_gateway_enabled ? 1 : 0

  name                = "pip-natgw-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = ["1", "2", "3"]
  tags                = local.tags
}

resource "azurerm_nat_gateway" "main" {
  count = var.nat_gateway_enabled ? 1 : 0

  name                    = "natgw-${local.name}"
  location                = var.location
  resource_group_name     = azurerm_resource_group.main.name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  tags                    = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "main" {
  count = var.nat_gateway_enabled ? 1 : 0

  nat_gateway_id       = azurerm_nat_gateway.main[0].id
  public_ip_address_id = azurerm_public_ip.natgw[0].id
}

resource "azurerm_subnet_nat_gateway_association" "aci" {
  count = var.nat_gateway_enabled ? 1 : 0

  subnet_id      = azurerm_subnet.aci.id
  nat_gateway_id = azurerm_nat_gateway.main[0].id
}

resource "azurerm_subnet_nat_gateway_association" "aca" {
  count = var.nat_gateway_enabled ? 1 : 0

  subnet_id      = azurerm_subnet.aca.id
  nat_gateway_id = azurerm_nat_gateway.main[0].id
}

# ── Agent subnet NSG ──────────────────────────────────────────────────────────

# This is the one subnet where DuckHaven has a security boundary to enforce: each
# agent runs an HTTP result server that the API scrapes over plain HTTP, and nothing
# else has any business reaching it.
#
# No NSG is attached to snet-aca. The Container Apps platform manages that subnet's
# connectivity, an incorrect rule breaks ingress in ways that are hard to diagnose,
# and the meaningful restriction lives here instead.
resource "azurerm_network_security_group" "aci" {
  name                = "nsg-aci-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# Validated during the Phase 0 spike: traffic from a Container Apps replica to a
# VNet address is SNAT'd to the infrastructure subnet (observed sources 10.x.0.4 and
# 10.x.1.183 from a /23 snet-aca), NOT the 100.100.x.x replica overlay address. So
# matching on the subnet prefix is correct.
resource "azurerm_network_security_rule" "aci_in_result_server" {
  name                        = "AllowContainerAppsToResultServer"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.aci.name
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_address_prefix       = var.subnet_prefix_aca
  source_port_range           = "*"
  destination_address_prefix  = var.subnet_prefix_aci
  destination_port_ranges     = [tostring(local.agent_result_port)]
}

# The default AllowVnetInBound rule sits at priority 65000 and would otherwise let
# any VNet source reach the agents. Deny it explicitly, after the allow above.
resource "azurerm_network_security_rule" "aci_in_deny_vnet" {
  name                        = "DenyAllOtherVnetInbound"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.aci.name
  priority                    = 200
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "*"
  source_address_prefix       = "VirtualNetwork"
  source_port_range           = "*"
  destination_address_prefix  = "*"
  destination_port_range      = "*"
}

# Outbound: an agent dials the API over TLS (via the NAT gateway), reaches Polaris
# and ADLS inside the VNet, and needs Azure DNS to resolve the privatelink zones.
# Nothing else.
resource "azurerm_network_security_rule" "aci_out_dns" {
  name                        = "AllowAzureDns"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.aci.name
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_address_prefix       = var.subnet_prefix_aci
  source_port_range           = "*"
  destination_address_prefix  = "168.63.129.16"
  destination_port_ranges     = ["53"]
}

resource "azurerm_network_security_rule" "aci_out_vnet" {
  name                        = "AllowVnetOutbound"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.aci.name
  priority                    = 110
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_address_prefix       = var.subnet_prefix_aci
  source_port_range           = "*"
  destination_address_prefix  = "VirtualNetwork"
  destination_port_range      = "*"
}

resource "azurerm_network_security_rule" "aci_out_https" {
  name                        = "AllowHttpsOutbound"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.aci.name
  priority                    = 120
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_address_prefix       = var.subnet_prefix_aci
  source_port_range           = "*"
  destination_address_prefix  = "Internet"
  destination_port_ranges     = ["443"]
}

resource "azurerm_network_security_rule" "aci_out_deny" {
  name                        = "DenyAllOtherOutbound"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.aci.name
  priority                    = 4000
  direction                   = "Outbound"
  access                      = "Deny"
  protocol                    = "*"
  source_address_prefix       = "*"
  source_port_range           = "*"
  destination_address_prefix  = "*"
  destination_port_range      = "*"
}

resource "azurerm_subnet_network_security_group_association" "aci" {
  subnet_id                 = azurerm_subnet.aci.id
  network_security_group_id = azurerm_network_security_group.aci.id
}

# ── Private DNS ───────────────────────────────────────────────────────────────

resource "azurerm_private_dns_zone" "main" {
  for_each = local.private_dns_zones

  name                = each.value
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# Linking every zone to the one VNet is what lets both Container Apps replicas and
# VNet-injected container groups resolve the private endpoints. Both paths were
# confirmed working during the Phase 0 spike.
resource "azurerm_private_dns_zone_virtual_network_link" "main" {
  for_each = local.private_dns_zones

  name                  = "link-${each.key}"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.main[each.key].name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = local.tags
}
