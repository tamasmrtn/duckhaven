/** How the main navigation is laid out. */
export const NAV_MODES = ["expanded", "collapsed", "hover"] as const;
export type NavMode = (typeof NAV_MODES)[number];

/** The app's colors; "system" follows the operating system's light or dark mode. */
export const THEMES = ["light", "dark", "system"] as const;
export type Theme = (typeof THEMES)[number];

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  theme: Theme;
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
