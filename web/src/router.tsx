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
import { SchedulesPage } from "@/features/schedules/SchedulesPage";
import { SessionsPage } from "@/features/sessions/SessionsPage";
import { SemanticPage } from "@/features/semantic/SemanticPage";
import { SemanticModelDetail } from "@/features/semantic/SemanticModelDetail";
import { SessionDetailPage } from "@/features/sessions/SessionDetailPage";
import { HistoryPage } from "@/features/history/HistoryPage";
import { QueryProfilePage } from "@/features/query-profile/QueryProfilePage";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AgentsPage } from "@/features/admin/AgentsPage";
import { AgentDetailPage } from "@/features/admin/AgentDetailPage";
import { StorageBackendsPage } from "@/features/admin/StorageBackendsPage";
import { CatalogMigrationsPage } from "@/features/admin/CatalogMigrationsPage";
import { UsersPage } from "@/features/admin/UsersPage";
import { ServiceAccountsPage } from "@/features/admin/ServiceAccountsPage";
import { CatalogAccessPage } from "@/features/admin/CatalogAccessPage";
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
  // The post-login landing: SSO callbacks redirect here, so resolve the
  // destination from the session rather than always bouncing to /login.
  // Authenticated -> first workspace (or /welcome when they have none);
  // unauthenticated -> /login.
  beforeLoad: async () => {
    try {
      await authApi.me();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        throw redirect({ to: "/login" });
      }
      throw err;
    }
    const workspaces = await workspacesApi.list();
    const first = workspaces[0];
    throw redirect(
      first
        ? { to: "/$ws/worksheets", params: { ws: first.slug } }
        : { to: "/welcome" },
    );
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

const catalogDetailRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/catalog/$catalog",
  component: CatalogPage,
});

const schemaDetailRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/catalog/$catalog/$schema",
  component: CatalogPage,
});

const catalogTableRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/catalog/$catalog/$schema/$table",
  component: CatalogPage,
  // Optional tab deep-link (e.g. from the worksheet's table hover-card, or a
  // command-palette result) so a caller can land straight on Lineage instead
  // of always defaulting to Sample.
  validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
    tab: typeof search.tab === "string" ? search.tab : undefined,
  }),
});

const savedQueriesRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/saved-queries",
  component: SavedQueriesPage,
});

const schedulesRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/schedules",
  component: SchedulesPage,
});

const sessionsRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/sessions",
  component: SessionsPage,
});

const sessionDetailRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/sessions/$sessionId",
  component: SessionDetailPage,
});

const semanticRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/semantic",
  component: SemanticPage,
});

const semanticModelRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/semantic/$model",
  component: SemanticModelDetail,
});

const historyRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/history",
  component: HistoryPage,
  // Optional agent filter (deep-linked from Admin → Agents → View audit).
  validateSearch: (search: Record<string, unknown>): { agent?: string } => ({
    agent: typeof search.agent === "string" ? search.agent : undefined,
  }),
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

// Compute sits alongside the other workspace sections, not under /admin: a user
// with a per-agent grant is entitled to their agent's status and monitoring page
// without holding any global admin permission. The fleet-level actions inside
// (new compute, bootstrap tokens) are gated on `agents:manage` within the page.
const computeRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/compute",
  component: AgentsPage,
});

const computeDetailRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/compute/$agentId",
  component: AgentDetailPage,
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
    throw redirect({ to: "/$ws/admin/storage", params: { ws: params.ws } });
  },
});

const storageRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/storage",
  component: StorageBackendsPage,
});

const migrationsRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/migrations",
  component: CatalogMigrationsPage,
});

const usersRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/users",
  component: UsersPage,
});

const serviceAccountsRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/service-accounts",
  component: ServiceAccountsPage,
});

const adminMaintenanceRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/maintenance",
  component: MaintenancePage,
});

const catalogAccessRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/catalog-access",
  component: CatalogAccessPage,
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  setupRoute,
  welcomeRoute,
  wsRoute.addChildren([
    worksheetsRoute,
    catalogRoute,
    catalogDetailRoute,
    schemaDetailRoute,
    catalogTableRoute,
    savedQueriesRoute,
    schedulesRoute,
    sessionsRoute,
    sessionDetailRoute,
    semanticRoute,
    semanticModelRoute,
    historyRoute,
    queryProfileRoute,
    healthRoute,
    computeRoute,
    computeDetailRoute,
    adminRoute.addChildren([
      adminIndexRoute,
      storageRoute,
      migrationsRoute,
      usersRoute,
      serviceAccountsRoute,
      adminMaintenanceRoute,
      catalogAccessRoute,
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
