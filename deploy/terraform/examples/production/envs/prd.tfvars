environment = "prd"

# Verify your subscription can actually provision PostgreSQL Flexible Server in this
# region before applying -- it is offer-restricted in some regions on some
# subscriptions, and the apply fails several minutes in when it is:
#   az postgres flexible-server list-skus -l <region>
# Prefer a region with three availability zones, so zone-redundant HA and ZRS storage
# are available.
location = "REPLACE_ME"

# Final segment of every resource name, making the globally-scoped ones unique to
# you. Exactly 3 lowercase alphanumeric characters. Keep it stable: those names stay
# reserved during their soft-delete window, so changing it is how you recover from a
# destroy/apply name collision.
name_suffix = "REPLACE_ME"

# Pin to a release tag before this environment carries real data: an image tag is
# how a deploy changes version, and `latest` makes that change invisible and
# irreversible. It also selects the agent image that provisioned container groups
# run, so it has to exist in the registry before the apply.
duckhaven_image_tag = "REPLACE_ME"

# Agents are provisioned into the delegated agent subnet with private addresses, so
# their result servers are reachable only from this virtual network.
elastic_compute_enabled = true

# The Terraform runner's egress address (`curl -s https://api.ipify.org`). Without it
# the storage firewall refuses the data-plane call that creates the warehouse
# filesystem, and the plan fails with that explanation.
management_plane_allowed_ips = []

# Addresses notified by the alert rules. Empty disables the action group and every
# alert with it, which is the right default only until someone is actually on the hook
# -- an alert nobody reads trains people to ignore the channel.
alert_email_addresses = []
