import {
  Outlet,
  useRouterState,
  useParams,
  useNavigate,
} from "@tanstack/react-router";
import { useMe } from "@/queries/auth";
import { cn } from "@/utils";

// Which global permission each admin section needs. Compute is deliberately
// absent: it moved out to the main nav, because a per-agent grantee is entitled
// to their agent's monitoring page without holding any global permission.
const tabs = [
  {
    segment: "storage",
    label: "Storage backends",
    permission: "storage:manage",
  },
  { segment: "migrations", label: "Migrations", permission: "storage:manage" },
  {
    segment: "maintenance",
    label: "Maintenance",
    permission: "maintenance:manage",
  },
  { segment: "users", label: "Users", permission: "users:manage" },
  {
    segment: "service-accounts",
    label: "Service accounts",
    permission: "service_accounts:manage",
  },
  {
    segment: "catalog-access",
    label: "Catalog access",
    permission: "catalogs:admin",
  },
];

export function AdminLayout() {
  const { ws } = useParams({ from: "/$ws/admin" });
  const state = useRouterState();
  const pathname = state.location.pathname;
  const navigate = useNavigate();
  const { data: me, isLoading } = useMe();
  const permissions = me?.permissions ?? [];
  const visibleTabs = tabs.filter(({ permission }) =>
    permissions.includes(permission),
  );

  // Client-side gate: the admin area's screens are individually enforced by the
  // API too, but non-admins shouldn't even see the admin shell (e.g. by typing
  // the URL directly). Wait for `me` to resolve so a real admin never flashes it.
  if (isLoading) return null;
  if (visibleTabs.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div className="space-y-1">
          <p className="text-sm font-medium text-text-primary">
            Admin access required
          </p>
          <p className="text-sm text-text-tertiary">
            You don&apos;t have permission to view this area.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Admin</h1>
        <nav className="mt-3 flex gap-1" aria-label="Admin sections">
          {visibleTabs.map(({ segment, label }) => {
            const active = pathname.includes(`/admin/${segment}`);
            return (
              <button
                key={segment}
                type="button"
                onClick={() =>
                  void navigate({ to: `/${ws}/admin/${segment}` as "/" })
                }
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-accent text-text-primary font-medium"
                    : "text-text-secondary hover:bg-accent/50 hover:text-text-primary",
                )}
                aria-current={active ? "page" : undefined}
              >
                {label}
              </button>
            );
          })}
        </nav>
      </div>
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
