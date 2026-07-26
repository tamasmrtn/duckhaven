subscription_id = "460eb8b9-9a3e-42f1-ab7f-d2963470725e"
environment     = "prod"

# France Central rather than Germany West Central: PostgreSQL Flexible Server is
# offer-restricted in GWC on this subscription. See variables.tf and
# docs/deployment/azure-terraform.md.
location       = "francecentral"
location_short = "frc"

# Keep this stable. Globally-unique names remain reserved during the soft-delete
# window, so changing it is how you recover from a destroy/apply name collision.
name_suffix = "a7k2m9"

# Pin to a release tag before this environment carries real data: an image tag is
# how a deploy changes version, and `latest` makes that change invisible and
# irreversible. It also selects the agent image that provisioned container groups
# run, so it has to exist in the registry before the apply.
duckhaven_image_tag = "latest"

log_retention_days = 30

# The public endpoints of Key Vault and the storage account stay open only to the
# addresses listed here, and only with Entra authentication. Replace with the CI
# runner's egress IP; set allow_management_plane_public_access = false once applies
# run from inside the VNet.
allow_management_plane_public_access = true
management_plane_allowed_ips         = []
