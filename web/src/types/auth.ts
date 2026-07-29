export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  theme: "light" | "dark" | "system";
  auth_provider: string;
  is_active: boolean;
  // Present on the authenticated user (`/me`); used to gate admin navigation.
  permissions?: string[];
  // True when the user holds any per-agent grant, directly or via a workspace.
  // Also `/me`-only. A grant is not a global permission, so it admits its holder
  // to the admin shell's Agents tab and to nothing else.
  agent_access?: boolean;
  created_at: string;
}

export interface OidcProviderInfo {
  id: string;
  label: string;
}

export interface AuthMethods {
  local: boolean;
  ldap: boolean;
  oidc_providers: OidcProviderInfo[];
}
