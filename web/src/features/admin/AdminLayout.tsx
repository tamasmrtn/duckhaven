import {
  Outlet,
  useRouterState,
  useParams,
  useNavigate,
} from "@tanstack/react-router";
import { cn } from "@/utils";

const tabs = [
  { segment: "agents", label: "Agents" },
  { segment: "metrics", label: "Utilization" },
  { segment: "storage", label: "Storage backends" },
  { segment: "users", label: "Users" },
  { segment: "audit", label: "Audit" },
];

export function AdminLayout() {
  const { ws } = useParams({ from: "/$ws/admin" });
  const state = useRouterState();
  const pathname = state.location.pathname;
  const navigate = useNavigate();

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
