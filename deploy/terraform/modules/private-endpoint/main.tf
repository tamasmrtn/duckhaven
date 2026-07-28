resource "azurerm_private_endpoint" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  # The caller passes local.tags, which carries the required keys; the rule cannot
  # see through a variable.
  # tflint-ignore: azurerm_resource_missing_tags
  tags = var.tags

  private_service_connection {
    name                           = "${var.name}-connection"
    private_connection_resource_id = var.target_resource_id
    subresource_names              = var.subresource_names
    is_manual_connection           = false
  }

  # The zone group is what writes the A records into the private DNS zone. Doing it
  # here rather than by hand matters for the registry and storage sub-resources,
  # which need more than one record (an ACR endpoint creates both the registry and a
  # regional "*.data" record).
  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = var.private_dns_zone_ids
  }
}
