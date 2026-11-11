import {
  Outlet,
  useRouterState,
  useParams,
  useNavigate,
} from "@tanstack/react-router";
import { useMe } from "@/queries/auth";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { PageHeader } from "@/components/ui/page-header";
import {
  segmentActiveClass,
  segmentGroupClass,
  segmentItemClass,
} from "@/components/ui/segmented";
import { cn } from "@/utils";

// Which global permission each admin section needs. Compute is deliberately
// absent: it moved out to the main nav, because a per-agent grantee is entitled
// to their agent's monitoring page without holding any global permission.
//
// `description` is the line under the page title. "Admin" alone does not say
// what any of its screens does, so the active section explains itself there.
const tabs = [
  {
    segment: "storage",
    label: "Storage backends",
    permission: "storage:manage",
    description: "Where workspace data is stored, and who uses each backend.",
  },
  {
    segment: "migrations",
    label: "Migrations",
    permission: "storage:manage",
    description: "Moves of a catalog's data between storage backends.",
  },
  {
    segment: "maintenance",
    label: "Maintenance",
    permission: "maintenance:manage",
    description: "How and when table health is scanned across the deployment.",
  },
  {
    segment: "users",
    label: "Users",
    permission: "users:manage",
    description: "Who can sign in, and the role each person holds.",
  },
  {
    segment: "service-accounts",
    label: "Service accounts",
    permission: "service_accounts:manage",
    description: "Accounts for tools and automation, and their tokens.",
  },
  {
    segment: "catalog-access",
    label: "Catalog access",
    permission: "catalogs:admin",
    description:
      "Which catalogs follow the workspace role, and which are scoped.",
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

  // Six screens share the "Admin" header, so the tab names the section.
  const section = visibleTabs.find(({ segment }) =>
    pathname.includes(`/admin/${segment}`),
  );
  useDocumentTitle(section ? `${section.label} · Admin` : "Admin");

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

  const active = visibleTabs.find(({ segment }) =>
    pathname.includes(`/admin/${segment}`),
  );

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Admin"
        description={active?.description}
        secondaryRow={
          <nav
            className={cn(segmentGroupClass, "mt-3 h-7")}
            aria-label="Admin sections"
          >
            {visibleTabs.map(({ segment, label }) => {
              const isActive = segment === active?.segment;
              return (
                <button
                  key={segment}
                  type="button"
                  onClick={() =>
                    void navigate({ to: `/${ws}/admin/${segment}` as "/" })
                  }
                  className={cn(
                    segmentItemClass,
                    isActive && segmentActiveClass,
                  )}
                  aria-current={isActive ? "page" : undefined}
                >
                  {label}
                </button>
              );
            })}
          </nav>
        }
      />
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
