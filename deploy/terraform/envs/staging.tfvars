subscription_id = "460eb8b9-9a3e-42f1-ab7f-d2963470725e"
environment     = "staging"

location       = "francecentral"
location_short = "frc"

name_suffix = "b3n8q4"

log_retention_days = 30

allow_management_plane_public_access = true
management_plane_allowed_ips         = []

# A separate VNet range so staging and prod can be peered or share a runner later
# without renumbering.
vnet_address_space = "10.43.0.0/16"
subnet_prefix_aca  = "10.43.0.0/23"
subnet_prefix_pe   = "10.43.2.0/24"
subnet_prefix_aci  = "10.43.3.0/24"
