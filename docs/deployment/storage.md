# Configure storage

A [storage backend](../concepts/storage-backends.md) is the object-storage location where a workspace's Iceberg tables
live. Admins register backends; each [workspace](../concepts/workspaces.md) binds to exactly one at creation.

## The bundled object store

Out of the box, name-only workspace creation uses the bundled object store (`object_store`), isolating each
workspace under a `/{slug}` prefix. No configuration is required to start. The store is
[RustFS](https://rustfs.com), an Apache-2.0 S3-compatible server, running as the `objectstore` service.

!!! warning "The bundled store is pinned to a release candidate"
    RustFS has not shipped a GA release yet; DuckHaven pins `1.0.0-rc.6`. The pin is deliberate — upgrades between
    release candidates have broken deployments upstream — so do not set `OBJECT_STORE_IMAGE_TAG` to `latest`.
    It replaced MinIO, whose Community Edition was archived in April 2026 and withdrawn from Docker Hub. If you are
    upgrading an existing deployment, see [Moving off the bundled MinIO](#moving-off-the-bundled-minio) below.

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
    The bundled store has no STS, so the S3 assume-role leg is exercised against LocalStack or a real AWS account (see
    `make localstack-dev`). Azure has no offline emulator for Entra credential vending, so the ADLS path is validated
    against a real Azure account.

## Bind a workspace

When creating a [workspace](../getting-started/first-workspace.md), select the registered backend. Every table in that
catalog lives under the backend's location. The binding is no longer permanent: an admin can later move a catalog to a
different backend with a [storage migration](../guides/migrate-catalog-storage.md).

## Moving off the bundled MinIO

Deployments created before the bundled store became RustFS keep their data in the `minio_data` Docker volume. The new
`objectstore` service uses a **new, empty** volume (`objectstore_data`) and does not read the old one, so after
upgrading the compose stack your tables are still on disk but the store in front of them is empty. Copy them across
before you rely on the upgraded stack.

!!! warning "Back up `minio_data` first"
    Nothing here writes to the old volume, but the copy is the only step between your tables and an empty bucket.
    Take a backup, and keep the old volume until you have confirmed a query reads real data.

### First: update your `.env`

The service is no longer called `minio`, and nothing answers to that name any more. If your `deploy/.env` pins the
endpoints — many do, because the old `.env.example` suggested it — change them before bringing the stack up:

```diff
-S3_ENDPOINT=http://minio:9000
-S3_ENDPOINT_INTERNAL=http://minio:9000
+S3_ENDPOINT=http://objectstore:9000
+S3_ENDPOINT_INTERNAL=http://objectstore:9000
```

Leaving them unset is also fine — the compose defaults are already correct. Miss this and the stack starts cleanly but
every catalog created afterwards records an endpoint that does not resolve, and its queries fail to reach storage. The
bundled backend's **Test access** button in Admin → Storage reports exactly this, so check it after the upgrade.

The copy runs S3-to-S3, with both stores up. Bring the stack up, then run the old store alongside it on a spare port:

```sh
cd deploy
docker compose up -d                       # starts objectstore and creates the bucket

docker run -d --name dh-minio-old \
  --network deploy_default -p 9500:9000 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  -v deploy_minio_data:/data \
  --entrypoint /bin/sh quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z \
  -c 'exec minio server /data'
```

Use the credentials your old stack actually ran with — `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from your `.env`, or
`minioadmin` if you never set them. Then mirror the bucket:

```sh
docker run --rm --network deploy_default rustfs/rc:v0.1.35 sh -c "
  rc alias set old http://dh-minio-old:9000 minioadmin minioadmin &&
  rc alias set new http://objectstore:9000 \"\$OBJECT_STORE_ACCESS_KEY\" \"\$OBJECT_STORE_SECRET_KEY\" &&
  rc mirror old/warehouse new/warehouse"
```

Confirm a table reads, then remove the old container (`docker rm -f dh-minio-old`). Keep the `minio_data` volume until
you are satisfied; deleting it is the irreversible step.

Catalogs created before the upgrade have the old endpoint recorded in Polaris, which is what Polaris vends to DuckDB.
You do not need to edit them: DuckHaven reconciles a catalog's stored endpoints with the configured ones the next time
the catalog is browsed, so each one heals itself on first use.
