# Configure storage

A [storage backend](../concepts/storage-backends.md) is the object-storage location where a workspace's Iceberg tables
live. Admins register backends; each [workspace](../concepts/workspaces.md) binds to exactly one at creation.

## The bundled object store

Out of the box, name-only workspace creation uses the bundled MinIO object store (`object_store`), isolating each
workspace under a `/{slug}` prefix. No configuration is required to start.

## Enable external storage types

Polaris must advertise the storage types you intend to use. The bundled stack sets
`SUPPORTED_CATALOG_STORAGE_TYPES=["S3","AZURE"]` in `deploy/docker-compose.yml`, which covers both AWS S3 and Azure
ADLS Gen2.

Credential vending happens inside Polaris, so no storage secrets ever reach the API or agents. The two clouds differ
in what Polaris itself needs:

- **AWS S3** — no static credentials anywhere; Polaris assumes the backend's IAM role via STS.
- **Azure ADLS Gen2** — Polaris mints SAS tokens through a service principal read from *its own* environment
  (`AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`, the Azure `DefaultAzureCredential` chain). Set these
  in `.env` to the SP that holds **Storage Blob Data Contributor** on the account; the per-backend config only carries
  the tenant id. Without them, ADLS vending fails.

## Register an AWS S3 backend

First, prepare AWS so Polaris can assume a least-privilege role:

1. **Create an IAM role** whose permission policy grants only the bucket and prefix you'll use — `s3:GetObject`,
   `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` scoped to `arn:aws:s3:::acme-data` and
   `arn:aws:s3:::acme-data/duckhaven/*`. Grant `kms:Decrypt`/`kms:GenerateDataKey` if the bucket uses SSE-KMS.
2. **Set the trust policy** so the Polaris principal can assume the role, guarded by your external id (a
   confused-deputy guard) and, where possible, an `aws:SourceArn` condition:

    ```json
    { "Version": "2012-10-17", "Statement": [{
      "Effect": "Allow",
      "Principal": { "AWS": "<polaris-principal-arn>" },
      "Action": "sts:AssumeRole",
      "Condition": { "StringEquals": { "sts:ExternalId": "dh-acme" } }
    }] }
    ```

3. **Harden the bucket**: block all public access, enforce TLS-only access with an `aws:SecureTransport` bucket
   policy, enable SSE-KMS, and reach it over a VPC gateway endpoint where the agents run.

Then in **Admin → Storage**, register a backend with kind `s3`, a root URI like `s3://acme-data/duckhaven/`, the
**role ARN**, **region**, and (recommended) the **external id**. Leave the endpoint blank for real AWS.

## Register an Azure ADLS Gen2 backend

1. **Register an Entra application** (or use Polaris's multi-tenant app) and grant it admin consent in your tenant via
   the consent URL.
2. **Assign the data-plane role** `Storage Blob Data Contributor` to that identity on the storage account or
   container — Entra RBAC, not account keys.
3. **Harden the account**: enable the hierarchical namespace (ADLS Gen2), restrict access with private endpoints, and
   keep encryption at rest on (default).

Then register a backend with kind `adls_gen2`, a root URI like
`abfss://research@acme.dfs.core.windows.net/duckhaven/`, the **tenant id**, and (if used) the app name / consent URL.
Turn on **hierarchical** for HNS accounts so SAS tokens are down-scoped to the path.

## Validate access

Each external backend row has a **Test access** button. It provisions a throwaway Polaris catalog from the config,
forces a storage write under the assumed role / consented app, then vends scoped client credentials and lists the
probe path — the same path agents use. A green result means register → vend → read/write works end to end; a red
result shows a sanitized reason (no secrets). A backend in use by any workspace cannot be deleted.

!!! note "Assume-role validation needs STS"
    The bundled MinIO has no STS, so the S3 assume-role leg is exercised against LocalStack or a real AWS account (see
    `make localstack-dev`). Azure has no offline emulator for Entra credential vending, so the ADLS path is validated
    against a real Azure account.

## Bind a workspace

When creating a [workspace](../getting-started/first-workspace.md), select the registered backend. The binding is
**immutable** afterwards — every table in that workspace lives under the backend's location.
