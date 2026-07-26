config {
  # Inspect the local private-endpoint module as it is actually called, so bad
  # arguments passed into it are caught here rather than at apply time.
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

# Enforce the tagging convention from locals.tf on every taggable resource, so the
# remaining phases cannot quietly add untagged infrastructure. Cost attribution and
# "what is this and who owns it" both depend on these three being present everywhere.
rule "azurerm_resource_missing_tags" {
  enabled = true
  tags    = ["app", "env", "managed-by"]
}

# Deletion protection for stateful resources is enforced on the Azure side, not with
# this meta-argument:
#
#   * Key Vault sets purge_protection_enabled (irreversible once on) with 90-day soft
#     delete, and the provider sets purge_soft_delete_on_destroy = false, so a destroy
#     leaves a recoverable vault rather than losing secrets.
#   * Postgres uses point-in-time restore plus geo-redundant backup; the storage
#     account uses blob and container soft delete.
#   * State lives in a versioned, Entra-only backend.
#
# lifecycle blocks cannot reference variables, so prevent_destroy is all-or-nothing
# across environments. Setting it would make the staging environment -- which exists
# to be created and destroyed -- undestroyable, and would break the documented
# teardown path, in exchange for protection Azure already provides more durably.
rule "azurerm_resources_missing_prevent_destroy" {
  enabled = false
}
