# terraform init -backend-config=backend.hcl
#
# Fill in storage_account_name from the bootstrap stack's backend_config output.
resource_group_name  = "rg-duckhaven-tfstate"
storage_account_name = "REPLACE_ME"
container_name       = "tfstate"
key                  = "quickstart.terraform.tfstate"

# No account key: the backend authenticates as the caller's Entra identity, matching
# shared_access_key_enabled = false on the state account.
use_azuread_auth = true
