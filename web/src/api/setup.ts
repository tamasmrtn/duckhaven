import type { User } from "@/types/auth";

export type SetupStatus = { needs_admin: boolean };

export type FirstAdminInput = {
  email: string;
  password: string;
  name?: string;
};

export const setupApi = {
  status: async (): Promise<SetupStatus> => {
    const res = await fetch("/api/setup/status", { credentials: "include" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json() as Promise<SetupStatus>;
  },

  createAdmin: async (token: string, input: FirstAdminInput): Promise<User> => {
    const res = await fetch("/api/setup/admin", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-Setup-Token": token,
      },
      body: JSON.stringify(input),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
      } catch {
        // ignore parse error
      }
      throw new Error(detail);
    }
    return res.json() as Promise<User>;
  },
};
