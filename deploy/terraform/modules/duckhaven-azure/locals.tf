locals {
  # ── Naming convention ───────────────────────────────────────────────────────
  #
  #   <abbr>-<env>-<workload>-<region>-<suffix>        kv-prd-duckhaven-frc-wej
  #
  # Following the Cloud Adoption Framework: the resource-type abbreviation leads, so a
  # resource group listing sorts by type, and components are hyphen-separated. The
  # abbreviations themselves are CAF's own (rg, kv, st, cr, cae, ca, caj, pgsql, vnet,
  # snet, nsg, pip, ng, pep, id, log, ag).
  #
  # Three resource types cannot take hyphens at all -- storage accounts and container
  # registries accept only letters and digits -- so they use `name_short`, the same
  # components concatenated.
  #
  # Resources scoped inside a parent that already carries the workload name (subnets in
  # the VNet, apps and jobs in the Container Apps environment) drop the workload segment:
  # `snet-prd-aca-frc-wej`, `ca-prd-api-frc-wej`. That is not only brevity -- a container
  # app's name is also its public DNS label, and the type caps at 32 characters.
  #
  # Key Vault is the binding constraint at 24 characters, and the default components
  # land on exactly 24. There is no headroom: a longer environment, workload or suffix
  # breaks it, which is why every one of them is fixed-length and why keyvault.tf
  # asserts the result at plan time rather than letting Azure reject it mid-apply.

  workload = "duckhaven"

  # Exactly three characters each, per the convention. Not exhaustive on purpose --
  # Azure has sixty-odd regions, and inventing an abbreviation for one nobody has tried
  # here would imply it had been. An uncovered region is handled by setting
  # location_short explicitly.
  #
  # Three characters is also what makes the US regions distinguishable: at two they all
  # collapse to a letter plus "us".
  location_short_codes = {
    francecentral      = "frc"
    germanywestcentral = "gwc"
    westeurope         = "weu"
    northeurope        = "neu"
    swedencentral      = "sdc"
    switzerlandnorth   = "chn"
    norwayeast         = "nwe"
    uksouth            = "uks"
    ukwest             = "ukw"
    eastus             = "eus"
    eastus2            = "eu2"
    westus             = "wus"
    westus2            = "wu2"
    westus3            = "wu3"
    centralus          = "cus"
    southcentralus     = "scu"
    canadacentral      = "cnc"
    australiaeast      = "aue"
    southeastasia      = "sea"
    japaneast          = "jpe"
  }

  # Empty rather than null when the region is uncovered, so the precondition in
  # main.tf reports it instead of coalesce failing with "no non-null arguments".
  location_short = (var.location_short != null
    ? var.location_short
    : lookup(local.location_short_codes, var.location, "")
  )

  # The uniqueness tail, shared by every name. The region because one workload can be
  # deployed to several; the suffix because storage accounts, key vaults and registries
  # share a single global namespace with every other Azure tenant.
  name_tail = "${local.location_short}-${var.name_suffix}"

  # <env>-<workload>-<region>-<suffix>. Call sites prefix the CAF abbreviation:
  # "kv-${local.name}" -> kv-prd-duckhaven-frc-wej.
  name = "${var.environment}-${local.workload}-${local.name_tail}"

  # The same components with no separators, for storage accounts and container
  # registries, whose names admit only letters and digits.
  name_short = "${var.environment}${local.workload}${local.location_short}${var.name_suffix}"

  # <env>-<region>-<suffix>, for resources scoped inside a parent that already names the
  # workload. Call sites insert the role: "snet-${var.environment}-aca-${local.name_tail}".
  # Kept as a comment rather than a local because the role sits in the middle.

  tags = merge(
    {
      app        = local.workload
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

  # Container app names. Scoped inside the environment, which already carries the
  # workload name, so they drop that segment -- and they have to, because a container
  # app's name is capped at 32 characters *and* becomes its public DNS label.
  api_app_name     = "ca-${var.environment}-api-${local.name_tail}"
  polaris_app_name = "ca-${var.environment}-polaris-${local.name_tail}"

  # The API's own public hostname, built from the environment's domain rather than read
  # off the app resource. Reading it back would make ELASTIC_CONTROL_PLANE_URL -- an
  # environment variable *on that app* -- depend on the app itself, which is a dependency
  # cycle Terraform cannot resolve. The domain is known as soon as the environment
  # exists, and an external-ingress app is always <name>.<domain>.
  api_fqdn = "${local.api_app_name}.${azurerm_container_app_environment.main.default_domain}"

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
      ? "https://${local.polaris_app_name}.${azurerm_container_app_environment.main.default_domain}"
      : "https://${local.polaris_app_name}.internal.${azurerm_container_app_environment.main.default_domain}"
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
