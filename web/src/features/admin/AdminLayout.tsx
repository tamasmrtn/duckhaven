import {
  Outlet,
  useRouterState,
  useParams,
  useNavigate,
} from "@tanstack/react-router";
import { useMe } from "@/queries/auth";
import { cn } from "@/utils";

const tabs = [
  { segment: "agents", label: "Agents" },
  { segment: "metrics", label: "Utilization" },
  { segment: "storage", label: "Storage backends" },
  { segment: "migrations", label: "Migrations" },
  { segment: "maintenance", label: "Maintenance" },
  { segment: "users", label: "Users" },
  { segment: "service-accounts", label: "Service accounts" },
  { segment: "catalog-access", label: "Catalog access" },
];

export function AdminLayout() {
  const { ws } = useParams({ from: "/$ws/admin" });
  const state = useRouterState();
  const pathname = state.location.pathname;
  const navigate = useNavigate();
  const { data: me, isLoading } = useMe();
  // Admin capability = holding any global permission (mirrors the left rail).
  const isAdmin = (me?.permissions?.length ?? 0) > 0;

  // Client-side gate: the admin area's screens are individually enforced by the
  // API too, but non-admins shouldn't even see the admin shell (e.g. by typing
  // the URL directly). Wait for `me` to resolve so a real admin never flashes it.
  if (isLoading) return null;
  if (!isAdmin) {
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
          {tabs.map(({ segment, label }) => {
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
