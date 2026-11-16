/** How the main navigation is laid out. */
export const NAV_MODES = ["expanded", "collapsed", "hover"] as const;
export type NavMode = (typeof NAV_MODES)[number];

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  theme: "light" | "dark" | "system";
  /** Null until the user chooses, which means the app's default. */
  sidebar?: NavMode | null;
  auth_provider: string;
  is_active: boolean;
  // Present on the authenticated user (`/me`); used to gate admin navigation.
  permissions?: string[];
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
