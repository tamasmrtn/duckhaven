# terraform init -reconfigure -backend-config=envs/staging.backend.hcl
resource_group_name  = "rg-duckhaven-tfstate"
storage_account_name = "REPLACE_ME"
container_name       = "tfstate"
key                  = "staging.terraform.tfstate"
use_azuread_auth     = true
