locals {
  # Short codes for the regions this deployment has been reasoned about in. Not
  # exhaustive on purpose -- Azure has sixty-odd regions and inventing an abbreviation
  # for one nobody has tried here would imply it had been. An uncovered region is
  # handled by setting location_short explicitly.
  location_short_codes = {
    francecentral      = "frc"
    germanywestcentral = "gwc"
    westeurope         = "weu"
    northeurope        = "neu"
    swedencentral      = "sdc"
    uksouth            = "uks"
    eastus             = "eus"
    eastus2            = "eus2"
    westus2            = "wus2"
    centralus          = "cus"
  }

  # Empty rather than null when the region is uncovered, so the precondition in
  # main.tf reports it instead of coalesce failing with "no non-null arguments".
  location_short = (var.location_short != null
    ? var.location_short
    : lookup(local.location_short_codes, var.location, "")
  )

  # Long form for resources whose names only need to be unique in the subscription.
  name = "duckhaven-${var.environment}-${local.location_short}"

  # Short form for globally-unique names, which are length-capped (storage accounts
  # allow 24 characters, key vaults 24). "dh" keeps headroom for the suffix.
  name_short = "dh${var.environment}${var.name_suffix}"

  tags = merge(
    {
      app        = "duckhaven"
      env        = var.environment
      managed-by = "terraform"
    },
    var.tags,
  )

  api_image_repository = "duckhaven-api"

  # Named separately because the registry pull token is scoped to exactly this repository.
  agent_image_repository = "duckhaven-agent"

  aca_workload_profile = "Consumption"

  # Port the agent result server listens on. The API fetches result Parquet from it
  # over plain HTTP, which is acceptable only because the hop stays inside the VNet;
  # nsg-aci restricts inbound on this port to the Container Apps subnet.
  #
  # A constant, not a variable: the agent binds 8001 unconditionally
  # (api/src/api/services/compute/azure_aci.py::_result_port), so a configurable value
  # here could only ever break the NSG rule.
  agent_result_port = 8001

  # Whether this stack owns the database server. Everything downstream reads the three
  # locals below rather than the resource directly, so bringing your own server changes
  # what they resolve to and nothing else.
  postgres_managed_here = var.postgres_existing_server_fqdn == null

  postgres_fqdn = (local.postgres_managed_here
    ? azurerm_postgresql_flexible_server.main[0].fqdn
    : var.postgres_existing_server_fqdn
  )
  duckhaven_database_name = var.postgres_database_name
  polaris_database_name   = var.postgres_polaris_database_name

  # The root principal the bootstrap creates and the API authenticates as. Not a
  # variable: it has to match api/src/api/config.py's polaris_client_id default, and
  # the two would drift silently if both were configurable.
  polaris_client_id = "root"

  # Mirrored into the registry rather than pulled from Docker Hub, so a deploy does not
  # depend on Docker Hub availability or anonymous pull limits. The import is a
  # prerequisite of the first apply -- see README.
  polaris_image            = "${azurerm_container_registry.main.login_server}/polaris:${var.polaris_image_tag}"
  polaris_admin_tool_image = "${azurerm_container_registry.main.login_server}/polaris-admin-tool:${var.polaris_image_tag}"

  # The API's own public hostname, built from the environment's domain rather than read
  # off the app resource. Reading it back would make ELASTIC_CONTROL_PLANE_URL -- an
  # environment variable *on that app* -- depend on the app itself, which is a dependency
  # cycle Terraform cannot resolve. The domain is known as soon as the environment
  # exists, and an external-ingress app is always <name>.<domain>.
  api_fqdn = "api.${azurerm_container_app_environment.main.default_domain}"

  # Polaris' hostname, which depends on how it has to be reached.
  #
  # An externally operated catalog is taken as given -- the operator decides how it is
  # exposed, and both the control plane and the agents reach it through this
  # deployment's NAT gateway, so one firewall rule on their side covers everything here.
  #
  # For a Polaris deployed by this stack, internal ingress is the default and the better
  # posture: the name resolves only inside the environment and there is no public
  # listener at all.
  #
  # Elastic compute forces the alternative. An agent runs in its own subnet, outside the
  # environment, and only replicas *inside* an environment resolve an internal-ingress
  # app -- measured from the agent subnet, Polaris' internal name resolved to the
  # environment's public address and returned 404. A private endpoint cannot fix it
  # either: Azure rejects one unless the environment's public access is disabled, which
  # would take the API offline with it. So when agents exist, Polaris takes external
  # ingress locked to this deployment's single egress address (see polaris.tf).
  #
  # The platform issues a certificate covering either name, so callers verify TLS
  # normally.
  polaris_url = (!var.polaris_enabled
    ? var.polaris_external_base_url
    : (var.elastic_compute_enabled
      ? "https://polaris.${azurerm_container_app_environment.main.default_domain}"
      : "https://polaris.internal.${azurerm_container_app_environment.main.default_domain}"
    )
  )

  # sslmode=require rather than the driver default of prefer: Azure enforces TLS, and
  # stating it means a misconfiguration fails loudly instead of silently downgrading.
  polaris_jdbc_url = join("", [
    "jdbc:postgresql://",
    local.postgres_fqdn,
    ":5432/",
    local.polaris_database_name,
    "?sslmode=require",
  ])

  # Zone redundancy, network rule sets and retention policies are Premium-only
  # registry features. Nothing about *access* depends on the SKU: every pull is
  # managed-identity authenticated on Basic just as on Premium.
  acr_is_premium = var.acr_sku == "Premium"

  # Private DNS zones needed by the private endpoints created in this deployment.
  # privatelink.azurecr.io is deliberately absent: the registry keeps a public
  # endpoint because Azure Container Instances pulls images from its own control
  # plane, outside the VNet, and ARM rejects a network-restricted registry at
  # pre-flight. See docs/deployment/azure-terraform.md.
  private_dns_zones = {
    postgres = "privatelink.postgres.database.azure.com"
    blob     = "privatelink.blob.core.windows.net"
    dfs      = "privatelink.dfs.core.windows.net"
    keyvault = "privatelink.vaultcore.azure.net"
  }
}
