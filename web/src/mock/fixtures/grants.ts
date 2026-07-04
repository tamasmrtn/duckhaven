import type { AccessMode, Grant, GrantPrincipal } from "@/types/grant";

// Mutable per-catalog state (keyed by catalog slug), rebuildable for test
// isolation via resetGrants().
export let ACCESS_MODE: Record<string, AccessMode> = {};
export let GRANTS: Record<string, Grant[]> = {};

// A small, fixed principal roster the grant editor picks from.
export const GRANT_PRINCIPALS: GrantPrincipal[] = [
  {
    user_id: "user-1",
    name: "Ada Lovelace",
    email: "ada@duckhaven.dev",
    role: "owner",
    is_service_account: false,
  },
  {
    user_id: "user-2",
    name: "Grace Hopper",
    email: "grace@duckhaven.dev",
    role: "reader",
    is_service_account: false,
  },
  {
    user_id: "sa-1",
    name: "ci-runner",
    email: "ci-runner@service-account.local",
    role: "reader",
    is_service_account: true,
  },
];

export function resetGrants(): void {
  ACCESS_MODE = {};
  GRANTS = {};
}
