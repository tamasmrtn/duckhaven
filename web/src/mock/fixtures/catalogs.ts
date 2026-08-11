import type { Catalog } from "@/types/catalog";
import type { CatalogSchema } from "@/types/catalog";

// Each workspace gets a default catalog (Polaris name == workspace slug, slug
// sanitized — mirrors the migration backfill). ws-1 also has a second catalog
// `curated` to exercise the multi-catalog tree, and that same catalog is
// attachable to other workspaces (M:N). Schemas for the non-default catalogs
// live here; default-catalog schemas reuse the per-workspace SCHEMAS store.

interface MockCatalog extends Catalog {
  workspace_ids: string[]; // bindings (M:N)
}

function makeCatalogs(): MockCatalog[] {
  return [
    {
      id: "cat-1",
      slug: "acme_analytics",
      name: "acme-analytics",
      polaris_name: "acme-analytics",
      storage_backend_id: "sb-1",
      storage_backend_kind: "s3",
      storage_backend_name: "acme-prod",
      storage_backend_root_uri: "s3://acme-data/duckhaven/",
      created_at: "2026-01-01T00:00:00Z",
      is_default: true,
      attached_workspaces: 1,
      workspace_ids: ["ws-1"],
    },
    {
      id: "cat-curated",
      slug: "curated",
      name: "Curated",
      polaris_name: "curated",
      storage_backend_id: "sb-1",
      storage_backend_kind: "s3",
      storage_backend_name: "acme-prod",
      storage_backend_root_uri: "s3://acme-data/duckhaven/",
      created_at: "2026-02-01T00:00:00Z",
      is_default: false,
      attached_workspaces: 1,
      workspace_ids: ["ws-1"],
    },
    {
      id: "cat-2",
      slug: "acme_research",
      name: "acme-research",
      polaris_name: "acme-research",
      storage_backend_id: "sb-2",
      storage_backend_kind: "adls_gen2",
      storage_backend_name: "research",
      storage_backend_root_uri:
        "abfss://research@acme.dfs.core.windows.net/duckhaven/",
      created_at: "2026-01-15T00:00:00Z",
      is_default: true,
      attached_workspaces: 1,
      workspace_ids: ["ws-2"],
    },
    {
      id: "cat-3",
      slug: "public",
      name: "public",
      polaris_name: "public",
      storage_backend_id: "sb-4",
      storage_backend_kind: "object_store",
      storage_backend_name: "box",
      storage_backend_root_uri: "",
      created_at: "2026-01-01T00:00:00Z",
      is_default: true,
      attached_workspaces: 1,
      workspace_ids: ["ws-3"],
    },
    {
      id: "cat-4",
      slug: "home_lab",
      name: "home-lab",
      polaris_name: "home-lab",
      storage_backend_id: "sb-3",
      storage_backend_kind: "object_store",
      storage_backend_name: "home-lab",
      storage_backend_root_uri: "home-lab/",
      created_at: "2026-02-01T00:00:00Z",
      is_default: true,
      attached_workspaces: 1,
      workspace_ids: ["ws-4"],
    },
  ];
}

export let CATALOGS = makeCatalogs();

// Schemas for non-default catalogs (default catalogs reuse SCHEMAS[wsId]).
function makeCatalogSchemas(): Record<string, CatalogSchema[]> {
  return {
    curated: [
      {
        name: "marts",
        catalog: "curated",
        workspace_id: "ws-1",
        tables: [
          {
            name: "revenue_daily",
            schema_name: "marts",
            catalog: "curated",
            workspace_id: "ws-1",
            row_count: 730,
            row_count_estimate: 730,
            size_bytes: 65536,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-15T02:00:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
            columns: [
              { position: 1, name: "d", type: "DATE", nullable: false },
              { position: 2, name: "revenue", type: "DOUBLE", nullable: false },
            ],
          },
        ],
      },
    ],
  };
}

export let CATALOG_SCHEMAS = makeCatalogSchemas();

export function resetCatalogs(): void {
  CATALOGS = makeCatalogs();
  CATALOG_SCHEMAS = makeCatalogSchemas();
}

export function catalogsForWorkspace(wsId: string): MockCatalog[] {
  return CATALOGS.filter((c) => c.workspace_ids.includes(wsId));
}

export function defaultCatalogSlug(wsId: string): string | undefined {
  return catalogsForWorkspace(wsId).find((c) => c.is_default)?.slug;
}

export function findCatalogById(id: string): MockCatalog | undefined {
  return CATALOGS.find((c) => c.id === id);
}
