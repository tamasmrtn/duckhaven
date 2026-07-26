# Terraform state backend

Creates the resource group, storage account and blob container that hold the main
stack's remote state. Run once per subscription.

This stack keeps **local state**, because it is what creates the remote backend
everything else uses. That state file is disposable — every resource here is trivially
recreatable, and `terraform import` recovers the stack if the file is lost. It is
gitignored.

```sh
terraform init
terraform apply \
  -var subscription_id=<sub> \
  -var storage_account_name=<globally-unique-name>
```

Then copy `storage_account_name` from the `backend_config` output into
`../envs/<env>.backend.hcl`.

The account sets `shared_access_key_enabled = false`, so the backend authenticates as
the caller's Entra identity (`use_azuread_auth = true`) and there is no account key to
leak. Whoever runs Terraform needs **Storage Blob Data Contributor** on the account.

Blob versioning and 30-day soft delete are on, which is the recovery path for a
corrupted or truncated state file.
