variable "name" {
  description = "Private endpoint name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group holding the endpoint."
  type        = string
}

variable "subnet_id" {
  description = "Subnet the endpoint NIC is placed in."
  type        = string
}

variable "target_resource_id" {
  description = "Resource ID of the service being exposed privately."
  type        = string
}

variable "subresource_names" {
  description = "Sub-resources to connect, e.g. [\"blob\"], [\"postgresqlServer\"], [\"vault\"]."
  type        = list(string)
}

variable "private_dns_zone_ids" {
  description = "Private DNS zones the endpoint registers its A records in."
  type        = list(string)
}

variable "tags" {
  description = "Tags applied to the endpoint."
  type        = map(string)
  default     = {}
}
