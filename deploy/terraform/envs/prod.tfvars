environment = "prod"

# Verify your subscription can actually provision PostgreSQL Flexible Server in this
# region before applying -- it is offer-restricted in some regions on some
# subscriptions, and the apply fails several minutes in when it is:
#   az postgres flexible-server list-skus -l <region>
# Prefer a region with three availability zones, so zone-redundant HA and ZRS storage
# are available.
location = "REPLACE_ME"

# Suffix for globally-unique names (storage account, registry, key vault). Pick 4-8
# lowercase alphanumeric characters and keep it stable: those names stay reserved
# during their soft-delete window, so changing it is how you recover from a
# destroy/apply name collision.
name_suffix = "REPLACE_ME"

# Pin to a release tag before this environment carries real data: an image tag is
# how a deploy changes version, and `latest` makes that change invisible and
# irreversible. It also selects the agent image that provisioned container groups
# run, so it has to exist in the registry before the apply.
duckhaven_image_tag = "REPLACE_ME"

log_retention_days = 30

# Agents are provisioned into the delegated agent subnet with private addresses, so
# their result servers are reachable only from this virtual network.
elastic_compute_enabled = true

# The public endpoints of Key Vault and the storage account stay open only to the
# addresses listed here, and only with Entra authentication. Set this to the CI
# runner's egress IP (`curl -s https://api.ipify.org`); set
# allow_management_plane_public_access = false once applies run from inside the VNet.
allow_management_plane_public_access = true
management_plane_allowed_ips         = []
