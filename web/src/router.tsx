import {
  createRouter,
  createRoute,
  createRootRoute,
  redirect,
} from "@tanstack/react-router";
import { LoadingScreen } from "@/components/app/LoadingScreen";
import { AppShell } from "@/components/app/AppShell";
import { LoginPage } from "@/features/auth/LoginPage";
import { WorksheetPage } from "@/features/worksheet/WorksheetPage";
import { CatalogPage } from "@/features/catalog/CatalogPage";
import { SavedQueriesPage } from "@/features/saved-queries/SavedQueriesPage";
import { HistoryPage } from "@/features/history/HistoryPage";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AgentsPage } from "@/features/admin/AgentsPage";
import { StorageBackendsPage } from "@/features/admin/StorageBackendsPage";
import { UsersPage } from "@/features/admin/UsersPage";
import { AuditPage } from "@/features/admin/AuditPage";

const rootRoute = createRootRoute();

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/login" });
  },
});

const wsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/$ws",
  component: AppShell,
});

const worksheetsRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/worksheets",
  component: WorksheetPage,
});

const catalogRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/catalog",
  component: CatalogPage,
});

const catalogTableRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/catalog/$schema/$table",
  component: CatalogPage,
});

const savedQueriesRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/saved-queries",
  component: SavedQueriesPage,
});

const historyRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/history",
  component: HistoryPage,
});

const adminRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/admin",
  component: AdminLayout,
});

const adminIndexRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/$ws/admin/agents", params: { ws: params.ws } });
  },
});

const agentsRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/agents",
  component: AgentsPage,
});

const storageRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/storage",
  component: StorageBackendsPage,
});

const usersRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/users",
  component: UsersPage,
});

const auditRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/audit",
  component: AuditPage,
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  wsRoute.addChildren([
    worksheetsRoute,
    catalogRoute,
    catalogTableRoute,
    savedQueriesRoute,
    historyRoute,
    adminRoute.addChildren([
      adminIndexRoute,
      agentsRoute,
      storageRoute,
      usersRoute,
      auditRoute,
    ]),
  ]),
]);

export const router = createRouter({
  routeTree,
  defaultPendingComponent: LoadingScreen,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
