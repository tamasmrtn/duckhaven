# Storage backends

A **storage backend** is a physical location where Iceberg table data lives. Backends are registered once by an admin
and referenced by [workspaces](workspaces.md) — one backend per workspace, fixed at creation.

## S3-compatible object storage only

Every backend is S3-compatible object storage. This is forced by DuckHaven's control-plane / compute split: DuckDB can
only read and write Iceberg tables through the REST catalog when [Polaris](catalogs.md) can vend scoped credentials the
remote [agent](agents.md) uses, which rules out local-file storage across the container boundary.

## Backend kinds

| Kind | Physical location | Required extension |
|---|---|---|
| `object_store` | Bundled MinIO bucket (per-workspace `/{slug}` prefix) | `httpfs` |
| `s3` | External, operator-owned S3 bucket | `httpfs` |
| `adls_gen2` | Azure Data Lake Storage Gen 2 | `azure` |

An agent must have the required DuckDB extension loaded to serve a workspace on a given backend; see the
[Agent reference](../reference/agent-reference.md).

!!! note "External cloud credentials are in progress"
    The bundled `object_store` path is fully wired. External `s3` / `adls_gen2` credential wiring is validated behind
    opt-in integration tests and is still being finished — check the roadmap before relying on it in production.

## Credential vending

When an agent attaches a workspace catalog, Polaris vends short-lived, connection-scoped credentials applied as a
DuckDB `SECRET` that dies with the per-query connection. No long-lived storage secrets are stored on agents.

## Related

- [Configure storage](../deployment/storage.md) — register and bind a backend.
- [Workspaces](workspaces.md) — how a workspace pins to a backend.
