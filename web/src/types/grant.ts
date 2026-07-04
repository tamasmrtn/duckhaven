export type GrantTier = "metadata" | "reader" | "writer";
export type AccessMode = "open" | "scoped";

export interface Grant {
  id: string;
  user_id: string;
  user_name: string | null;
  schema_name: string | null;
  table_name: string | null;
  tier: string;
  created_at: string;
}

export interface GrantPrincipal {
  user_id: string;
  name: string;
  email: string;
  role: string;
  is_service_account: boolean;
}

export interface CatalogGrants {
  access_mode: AccessMode;
  grants: Grant[];
  principals: GrantPrincipal[];
}

export interface GrantUpsertInput {
  user_id: string;
  schema_name?: string | null;
  table_name?: string | null;
  tier: GrantTier;
}
