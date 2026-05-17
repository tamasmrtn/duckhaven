export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "user";
  theme: "light" | "dark" | "system";
  created_at: string;
}
