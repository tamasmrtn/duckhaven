# External storage: security & maintenance

This page covers the day-2 practice for external [S3 / ADLS Gen2 backends](../concepts/storage-backends.md): securing
access, laying out data, and the ongoing maintenance routine. For first-time registration, see
[Configure storage](../deployment/storage.md).

## Security baseline

DuckHaven enforces access **only** through Polaris credential vending: the API and agents never hold a long-lived
storage key, and the role assumption / SAS minting happens server-side. Everything else is enforced in your cloud
account — DuckHaven cannot substitute for it:

| Control | AWS S3 | Azure ADLS Gen2 |
|---|---|---|
| Identity | IAM role assumed via STS (`roleArn` + `externalId`) | Entra app + managed identity (`tenantId`, consent) |
| Least privilege | Bucket/prefix-scoped role policy | `Storage Blob Data Contributor` on the account/container |
| Confused-deputy guard | `externalId` + `aws:SourceArn` condition | Single-tenant consent |
| Encryption at rest | SSE-KMS (scope the key in the role policy) | Account encryption (default), customer-managed keys optional |
| Network | Block public access, TLS-only policy, VPC endpoint | Private endpoints, firewall rules |

Rotate nothing by hand: STS credentials and SAS tokens are short-lived and minted per connection. To revoke access,
remove the role trust (S3) or the RBAC assignment / consent (ADLS) in the cloud — the next vend then fails and the
**Test access** check goes red.

## Data layout & isolation

Each catalog is scoped to a `/{catalog-name}` prefix under its backend's root URI (added when the Polaris catalog is
provisioned), so catalogs on one backend never collide. Choose the isolation boundary that matches your blast-radius
needs:

- **Shared backend, prefix isolation (default)** — one bucket/container, many catalogs under distinct prefixes. Simple
  and adequate when one IAM role / identity may reach the whole bucket.
- **Per-team backend** — register a separate `s3` / `adls_gen2` backend with its own bucket (or container) and its own
  role/identity when teams must be cryptographically isolated. This is an operator decision; DuckHaven does not create
  buckets for you.

Set lifecycle and retention policies on the bucket/container itself (e.g. S3 lifecycle rules, ADLS lifecycle
management) for cost control, and keep data in the same region as the agents to avoid cross-region transfer cost and
latency.

## Maintenance routine

Iceberg tables accumulate snapshots, small files, and orphaned data over time. DuckHaven's
[maintenance advisor](../concepts/maintenance.md) scans every catalog — including those on external backends — scores
table health, and recommends `expire_snapshots`, `compact_small_files`, manifest rewrites, and `cleanup_orphans`. The
advisor is read-only; run the recommended operation with your write engine (Spark / PyIceberg) against the same
catalog.

A practical cadence for a mid-sized org:

- **Weekly** — review the [Lakehouse health](../concepts/maintenance.md) recommendations; expire snapshots past the
  retention target and compact tables flagged for small files.
- **Monthly** — run orphan-file cleanup on high-churn tables; confirm bucket lifecycle rules are pruning expired
  snapshot data.
- **Quarterly** — re-run **Test access** on each backend after any cloud-side IAM / RBAC change, and review that role
  policies are still least-privilege.

Storage backups remain a cloud-native concern: enable S3 versioning / cross-region replication or ADLS
soft-delete / redundancy as your durability target requires. (DuckHaven's own [backups](runbook.md) cover only the
Postgres control-plane state, not the object data.)
