environment = "stg"

# See envs/prd.tfvars for how to check the region is usable before applying.
location = "REPLACE_ME"

# Exactly 3 lowercase alphanumeric characters, globally unique to you.
name_suffix = "REPLACE_ME"

duckhaven_image_tag = "REPLACE_ME"

# Deliberately production-shaped: zone-redundant Postgres with a standby, ZRS storage,
# a Premium registry, two replicas of each app. This environment exists to verify that
# the production topology applies and works, and an environment that differs from
# production does not verify production.
#
# It therefore costs roughly what production costs. If what you want is a cheap
# environment to try DuckHaven in, use examples/quickstart -- it keeps the same network
# topology and drops the SKUs. And whichever you use, the largest saving is not a
# setting: everything here bills for existing rather than for being used, so
# `terraform destroy` between test sessions saves more than any sizing choice.
# `az postgres flexible-server stop` pauses compute for up to 7 days if the data needs
# keeping.

elastic_compute_enabled = true

# The Terraform runner's egress address (`curl -s https://api.ipify.org`).
management_plane_allowed_ips = []

# Nobody is on the hook for a staging alert, and an alert nobody reads trains people to
# ignore the channel.
alert_email_addresses = []
