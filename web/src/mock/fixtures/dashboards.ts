import type {
  ChartSpec,
  Dashboard,
  DashboardFile,
  RunColumn,
  RunWidget,
  WidgetScope,
} from "@/types/dashboard";

// Three dashboards in acme-analytics: one published from the app, one managed
// in git whose widgets show the states a viewer without access, a broken query
// and an empty window produce, and one draft nobody has published.

const LAST_30: WidgetScope = {
  window: {
    start: "2026-10-15",
    end: "2026-11-14",
    text: "Last 30 days: 15 Oct – 13 Nov 2026",
  },
  compare_window: null,
  applied: ["period"],
  skipped: [],
};

function chart(
  type: ChartSpec["type"],
  changes: Partial<ChartSpec> = {},
): ChartSpec {
  return {
    type,
    x: null,
    y: [],
    series: null,
    stack: false,
    orientation: null,
    colors: {},
    limits: { rows: null, series: 8 },
    ...changes,
  };
}

const REVENUE: RunColumn = {
  name: "revenue",
  type: "DECIMAL(18,2)",
  role: "measure",
  label: "Revenue",
  format: "currency",
  unit: null,
  currency: "USD",
};

const ORDERS: RunColumn = {
  name: "orders",
  type: "BIGINT",
  role: "measure",
  label: "Orders",
  format: "number",
  unit: "orders",
  currency: null,
};

const REVENUE_DEFINITION = {
  kind: "metric" as const,
  model: "sales",
  name: "revenue",
  label: "Revenue",
  description: "Paid order value, before refunds.",
  expression: "SUM(total_amount) FILTER (WHERE status = 'paid')",
  caveat: "Excludes internal test orders.",
};

function answered(
  id: string,
  query: number,
  rest: Omit<RunWidget, "type" | "id" | "state" | "scope"> & {
    scope?: WidgetScope;
    state?: RunWidget["state"];
  },
): RunWidget {
  return {
    type: "widget",
    id,
    state: "ok",
    scope: LAST_30,
    cache: "miss",
    warnings: [],
    ...rest,
    rows: rest.rows && { ...rest.rows, query_id: `q-dash-${query}` },
  };
}

const MONTHS = [
  "2026-06-01",
  "2026-07-01",
  "2026-08-01",
  "2026-09-01",
  "2026-10-01",
  "2026-11-01",
];

const SALES_FILE: DashboardFile = {
  version: 1,
  name: "sales-overview",
  title: "Sales overview",
  description:
    "Revenue and orders across the shop, refreshed from the orders table.",
  labels: ["sales"],
  parameters: {
    period: { type: "time_range", default: "last_30_days" },
    country: {
      type: "dimension",
      model: "sales",
      dimension: "country",
      label: "Country",
    },
  },
  rows: [
    {
      widgets: [
        {
          id: "revenue",
          type: "kpi",
          title: "Revenue",
          query: { model: "sales" },
        },
        {
          id: "orders",
          type: "kpi",
          title: "Orders",
          query: { model: "sales" },
        },
        {
          id: "refund_rate",
          type: "kpi",
          title: "Refund rate",
          query: { model: "sales" },
        },
      ],
    },
    {
      widgets: [
        {
          id: "revenue_trend",
          type: "line",
          title: "Revenue by month",
          width: 8,
          query: { model: "sales", dimensions: ["channel"] },
        },
        {
          id: "by_country",
          type: "bar",
          title: "Revenue by country",
          width: 4,
          query: { model: "sales", dimensions: ["country"] },
        },
      ],
    },
    {
      widgets: [
        {
          id: "latest_orders",
          type: "table",
          title: "Latest orders",
          width: 8,
        },
        {
          id: "notes",
          type: "text",
          width: 4,
          markdown:
            "**Revenue** counts paid orders only. See [the metric](https://duckhaven.local/acme-analytics/semantic/sales) for its definition.",
        },
      ],
    },
  ],
};

export const SALES_RUN: RunWidget[] = [
  answered("revenue", 1, {
    rows: {
      columns: [REVENUE],
      data: [["48210.50"]],
      truncated: false,
      query_id: "",
    },
    chart: chart("kpi", {
      y: [
        {
          field: "revenue",
          label: "Revenue",
          format: "currency",
          currency: "USD",
          polarity: "up_good",
        },
      ],
      limits: { rows: 1, series: 8 },
    }),
    compare: {
      current: "48210.50",
      previous: "42860.00",
      delta: "5350.50",
      delta_pct: 0.124837,
      good: true,
      window_text: "Previous 30 days: 15 Sep – 14 Oct 2026",
    },
    definitions: [REVENUE_DEFINITION],
    sql: "SELECT SUM(total_amount) FILTER (WHERE status = 'paid') AS revenue\nFROM warehouse.analytics.orders\nWHERE order_date >= DATE '2026-10-15' AND order_date < DATE '2026-11-14'",
    warnings: ["revenue: Excludes internal test orders."],
  }),
  answered("orders", 1, {
    rows: { columns: [ORDERS], data: [[1284]], truncated: false, query_id: "" },
    chart: chart("kpi", {
      y: [
        { field: "orders", label: "Orders", format: "number", unit: "orders" },
      ],
      limits: { rows: 1, series: 8 },
    }),
    sql: "SELECT COUNT(id) AS orders FROM warehouse.analytics.orders",
    cache: "hit",
  }),
  answered("refund_rate", 2, {
    rows: {
      columns: [
        {
          name: "refund_rate",
          type: "DOUBLE",
          role: "measure",
          label: "Refund rate",
          format: "percent",
          unit: null,
          currency: null,
        },
      ],
      data: [[0.031]],
      truncated: false,
      query_id: "",
    },
    chart: chart("kpi", {
      y: [
        {
          field: "refund_rate",
          label: "Refund rate",
          format: "percent",
          polarity: "down_good",
        },
      ],
      limits: { rows: 1, series: 8 },
    }),
    compare: {
      current: 0.031,
      previous: 0.027,
      delta: 0.004,
      delta_pct: 0.148148,
      good: false,
      window_text: "Previous 30 days: 15 Sep – 14 Oct 2026",
    },
    sql: "SELECT AVG(refunded::INT) AS refund_rate FROM warehouse.analytics.orders",
  }),
  answered("revenue_trend", 3, {
    rows: {
      columns: [
        { name: "month", type: "DATE", role: "time" },
        { name: "channel", type: "VARCHAR", role: "dimension" },
        REVENUE,
      ],
      data: MONTHS.flatMap((m, i) => [
        [m, "Web", String(26000 + i * 1800)],
        [m, "Retail", String(15000 + i * 700 - (i % 2) * 900)],
      ]),
      truncated: false,
      query_id: "",
    },
    chart: chart("line", {
      x: { field: "month", kind: "time", grain: "month" },
      y: [
        {
          field: "revenue",
          label: "Revenue",
          format: "currency",
          currency: "USD",
          polarity: "up_good",
        },
      ],
      series: { field: "channel", keys: ["Web", "Retail"] },
      colors: { Web: 0, Retail: 3 },
    }),
    definitions: [REVENUE_DEFINITION],
    sql: "SELECT date_trunc('month', order_date) AS month, channel, SUM(total_amount) AS revenue\nFROM warehouse.analytics.orders GROUP BY 1, 2",
    scope: {
      ...LAST_30,
      window: {
        start: "2026-06-01",
        end: "2026-12-01",
        text: "Last 6 months: Jun – Nov 2026",
      },
    },
  }),
  answered("by_country", 4, {
    rows: {
      columns: [
        { name: "country", type: "VARCHAR", role: "dimension" },
        REVENUE,
      ],
      data: [
        ["NL", "18420.00"],
        ["DE", "12210.50"],
        ["BE", "8130.00"],
        ["FR", "6050.00"],
        ["US", "3400.00"],
      ],
      truncated: false,
      query_id: "",
    },
    chart: chart("bar", {
      x: { field: "country", kind: "category" },
      y: [
        {
          field: "revenue",
          label: "Revenue",
          format: "currency",
          currency: "USD",
          polarity: "up_good",
        },
      ],
      orientation: "horizontal",
      limits: { rows: 10, series: 8 },
    }),
    definitions: [REVENUE_DEFINITION],
    sql: "SELECT country, SUM(total_amount) AS revenue FROM warehouse.analytics.orders GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
    scope: {
      ...LAST_30,
      skipped: [{ parameter: "country", reason: "own_dimension" }],
    },
  }),
  answered("latest_orders", 5, {
    rows: {
      columns: [
        { name: "id", type: "BIGINT", role: null },
        { name: "customer", type: "VARCHAR", role: null },
        { name: "country", type: "VARCHAR", role: null },
        { name: "total_amount", type: "DECIMAL(18,2)", role: null },
        { name: "order_date", type: "DATE", role: null },
      ],
      data: Array.from({ length: 240 }, (_, i) => [
        String(9000 - i),
        `Customer ${(i % 37) + 1}`,
        ["NL", "DE", "BE", "FR", "US"][i % 5],
        (20 + ((i * 37) % 400) + 0.5).toFixed(2),
        `2026-11-${String(13 - (i % 13)).padStart(2, "0")}`,
      ]),
      truncated: false,
      query_id: "",
    },
    chart: chart("table", { limits: { rows: 1000, series: 8 } }),
    sql: "SELECT id, customer, country, total_amount, order_date\nFROM warehouse.analytics.orders\nWHERE order_date >= $since ORDER BY id DESC",
  }),
];

const FINANCE_FILE: DashboardFile = {
  version: 1,
  name: "finance-weekly",
  title: "Finance weekly",
  description: "Margin and payouts, maintained by the finance team in git.",
  labels: ["finance"],
  parameters: { period: { type: "time_range", default: "last_7_days" } },
  rows: [
    {
      widgets: [
        { id: "margin", type: "kpi", title: "Gross margin" },
        { id: "payouts", type: "table", title: "Payouts" },
        { id: "refunds", type: "bar", title: "Refunds by reason" },
      ],
    },
  ],
};

export const FINANCE_RUN: RunWidget[] = [
  {
    type: "widget",
    id: "margin",
    state: "no_access",
    scope: LAST_30,
    error: "You don't have access to the data this widget reads.",
  },
  {
    type: "widget",
    id: "payouts",
    state: "error",
    scope: LAST_30,
    error: "Catalog Error: Table with name payouts does not exist!",
  },
  answered("refunds", 6, {
    state: "empty",
    rows: {
      columns: [{ name: "reason", type: "VARCHAR", role: "dimension" }],
      data: [],
      truncated: false,
      query_id: "",
    },
    chart: chart("bar", { x: { field: "reason", kind: "category" } }),
  }),
];

const CHURN_FILE: DashboardFile = {
  version: 1,
  name: "churn-review",
  title: "Churn review",
  rows: [
    { widgets: [{ id: "churned", type: "kpi", title: "Churned customers" }] },
  ],
};

export const CHURN_RUN: RunWidget[] = [
  answered("churned", 7, {
    rows: {
      columns: [
        { ...ORDERS, name: "churned", label: "Churned", unit: "customers" },
      ],
      data: [[42]],
      truncated: false,
      query_id: "",
    },
    chart: chart("kpi", {
      y: [
        {
          field: "churned",
          label: "Churned",
          unit: "customers",
          polarity: "down_good",
        },
      ],
    }),
  }),
];

function dashboard(
  file: DashboardFile,
  changes: Partial<Dashboard>,
): Dashboard {
  return {
    id: `dash-${file.name}`,
    name: file.name,
    title: file.title,
    description: file.description ?? null,
    labels: file.labels ?? [],
    status: "published",
    has_draft: false,
    source: "ui",
    source_id: null,
    managed: false,
    version: 3,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-11-12T16:20:00Z",
    updated_by: "u-1",
    updated_via: "ui",
    published_at: "2026-11-12T16:20:00Z",
    draft: false,
    spec: file,
    source_text: null,
    spec_hash: `hash-${file.name}`,
    spec_version: 1,
    source_ref: null,
    client: null,
    created_by: "u-1",
    published_by: "u-1",
    refs: [],
    warnings: [],
    ...changes,
  };
}

function initial(): Dashboard[] {
  return [
    dashboard(SALES_FILE, { has_draft: true }),
    dashboard(FINANCE_FILE, {
      source: "git",
      source_id: "acme/dashboards",
      managed: true,
      updated_via: "cli",
    }),
    dashboard(CHURN_FILE, {
      status: "draft",
      draft: true,
      published_at: null,
      published_by: null,
      version: 1,
    }),
  ];
}

/** The dashboards of acme-analytics (ws-1). */
export let DASHBOARDS: Dashboard[] = initial();

/** What each dashboard's run answers, and the widgets that wait for compute first. */
export const RUNS: Record<string, { widgets: RunWidget[]; cold?: string[] }> = {
  "sales-overview": { widgets: SALES_RUN },
  "finance-weekly": { widgets: FINANCE_RUN, cold: ["refunds"] },
  "churn-review": { widgets: CHURN_RUN },
};

export function resetDashboards(): void {
  DASHBOARDS = initial();
}
