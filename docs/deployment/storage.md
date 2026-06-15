# Configure storage

A [storage backend](../concepts/storage-backends.md) is the object-storage location where a workspace's Iceberg tables
live. Admins register backends; each [workspace](../concepts/workspaces.md) binds to exactly one at creation.

## The bundled object store

Out of the box, name-only workspace creation uses the bundled MinIO object store (`object_store`), isolating each
workspace under a `/{slug}` prefix. No configuration is required to start.

## Register an external backend

From **Admin → Storage**, register a backend with its kind, a name, and a root URI:

| Kind | Root URI example | Required agent extension |
|---|---|---|
| `s3` | `s3://acme-data/duckhaven/` | `httpfs` |
| `adls_gen2` | `abfss://research@acme/duckhaven/` | `azure` |

Registration runs a health check (credential vending plus a test `LIST` against the root), naming the agent that ran
it. A backend that is in use by any workspace cannot be deleted.

!!! note "External cloud credentials are in progress"
    The bundled `object_store` path is fully wired. External `s3` / `adls_gen2` credential wiring (role/tenant) is
    validated behind opt-in integration tests and is still being finished — verify against the roadmap before relying
    on it in production.

## Bind a workspace

When creating a [workspace](../getting-started/first-workspace.md), select the registered backend. The binding is
**immutable** afterwards — every table in that workspace lives under the backend's location.
