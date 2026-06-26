import {
  createRouter,
  createRoute,
  createRootRoute,
  redirect,
  notFound,
} from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { authApi } from "@/api/auth";
import { workspacesApi } from "@/api/workspaces";
import { LoadingScreen } from "@/components/app/LoadingScreen";
import { NotFoundPage } from "@/features/app/NotFoundPage";
import { AppShell } from "@/components/app/AppShell";
import { LoginPage } from "@/features/auth/LoginPage";
import { SetupPage } from "@/features/auth/SetupPage";
import { WelcomePage } from "@/features/auth/WelcomePage";
import { WorksheetPage } from "@/features/worksheet/WorksheetPage";
import { CatalogPage } from "@/features/catalog/CatalogPage";
import { SavedQueriesPage } from "@/features/saved-queries/SavedQueriesPage";
import { HistoryPage } from "@/features/history/HistoryPage";
import { QueryProfilePage } from "@/features/query-profile/QueryProfilePage";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AgentsPage } from "@/features/admin/AgentsPage";
import { MetricsPage } from "@/features/admin/MetricsPage";
import { StorageBackendsPage } from "@/features/admin/StorageBackendsPage";
import { UsersPage } from "@/features/admin/UsersPage";
import { MaintenancePage } from "@/features/admin/MaintenancePage";
import { LakehouseHealthPage } from "@/features/health/LakehouseHealthPage";

const rootRoute = createRootRoute();

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/setup",
  component: SetupPage,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/login" });
  },
});

const welcomeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/welcome",
  component: WelcomePage,
});

const wsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/$ws",
  component: AppShell,
  notFoundComponent: NotFoundPage,
  // Centralized guard for every workspace route: require a session, then a
  // real workspace. Logged-out deep links redirect to /login; unknown
  // workspace slugs render the not-found page instead of a blank shell.
  beforeLoad: async ({ params }) => {
    try {
      await authApi.me();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        throw redirect({ to: "/login" });
      }
      throw err;
    }
    try {
      await workspacesApi.get(params.ws);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        throw notFound();
      }
      throw err;
    }
  },
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
  path: "/catalog/$catalog/$schema/$table",
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

const queryProfileRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/queries/$queryId",
  component: QueryProfilePage,
});

const healthRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/health",
  component: LakehouseHealthPage,
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

const metricsRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/metrics",
  component: MetricsPage,
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

const adminMaintenanceRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/maintenance",
  component: MaintenancePage,
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  setupRoute,
  welcomeRoute,
  wsRoute.addChildren([
    worksheetsRoute,
    catalogRoute,
    catalogTableRoute,
    savedQueriesRoute,
    historyRoute,
    queryProfileRoute,
    healthRoute,
    adminRoute.addChildren([
      adminIndexRoute,
      agentsRoute,
      metricsRoute,
      storageRoute,
      usersRoute,
      adminMaintenanceRoute,
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
