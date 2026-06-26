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
  created_at: string;
}

export interface AuthMethods {
  local: boolean;
  ldap: boolean;
  oidc: boolean;
  oidc_label: string;
}
