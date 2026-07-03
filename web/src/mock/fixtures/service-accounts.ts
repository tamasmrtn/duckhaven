import type { Pat, ServiceAccount } from "@/types/service-account";

// POST/PATCH/DELETE mutate these stores, so both are rebuildable via
// resetServiceAccounts() for test isolation.
function makeServiceAccounts(): ServiceAccount[] {
  return [
    {
      id: "sa-1",
      name: "ci-runner",
      email: "ci-runner@service-account.local",
      role: "user",
      is_active: true,
      created_at: "2026-02-01T00:00:00Z",
      pat_count: 1,
    },
    {
      id: "sa-2",
      name: "nightly-dbt",
      email: "nightly-dbt@service-account.local",
      role: "user",
      is_active: false,
      created_at: "2026-03-01T00:00:00Z",
      pat_count: 0,
    },
  ];
}

function makePats(): Record<string, Pat[]> {
  return {
    "sa-1": [
      {
        id: "pat-1",
        created_at: "2026-02-02T00:00:00Z",
        expires_at: "2026-05-02T00:00:00Z",
      },
    ],
    "sa-2": [],
  };
}

export let SERVICE_ACCOUNTS = makeServiceAccounts();
export let SA_PATS: Record<string, Pat[]> = makePats();

export function resetServiceAccounts(): void {
  SERVICE_ACCOUNTS = makeServiceAccounts();
  SA_PATS = makePats();
}
