import {
  FileText,
  BookOpen,
  Bookmark,
  CalendarClock,
  Clock,
  Microchip,
  Plug,
  Settings,
  SlidersHorizontal,
  HeartPulse,
  LayoutDashboard,
  Ruler,
} from "lucide-react";

/** The navigation's sections, in the order they are shown. */
export const navGroups = ["SQL", "Data", "Analytics", "Manage"] as const;
export type NavGroup = (typeof navGroups)[number];

export type NavSegment =
  | "worksheets"
  | "saved-queries"
  | "schedules"
  | "history"
  | "sessions"
  | "catalog"
  | "health"
  | "dashboards"
  | "semantic"
  | "compute"
  | "admin"
  | "settings";

export interface NavItem {
  segment: NavSegment;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  group: NavGroup;
  // When true, the item is only shown to users holding at least one global
  // permission (the admin section is enforced server-side regardless).
  requiresAdmin?: boolean;
  // When true, the item is a real destination (surfaced in the command
  // palette) but has no entry in the navigation itself — e.g. Settings, which
  // lives in the user menu instead.
  hiddenFromRail?: boolean;
  // Other route segments that belong to this destination, so it stays marked
  // as the current page on them.
  alsoActiveOn?: string[];
}

// In navigation order, group by group; the command palette lists them in this
// order too.
export const navItems: NavItem[] = [
  {
    segment: "worksheets",
    icon: FileText,
    label: "Worksheets",
    group: "SQL",
  },
  {
    segment: "saved-queries",
    icon: Bookmark,
    label: "Saved queries",
    group: "SQL",
  },
  {
    segment: "schedules",
    icon: CalendarClock,
    label: "Schedules",
    group: "SQL",
  },
  {
    segment: "history",
    icon: Clock,
    label: "History",
    group: "SQL",
    // A connection's page and a query's profile are both reached from History.
    alsoActiveOn: ["sessions", "queries"],
  },
  {
    segment: "sessions",
    icon: Plug,
    label: "Connections",
    group: "SQL",
    // A view of History (/sessions redirects there), so it has no entry of its
    // own; the palette still finds it by name.
    hiddenFromRail: true,
  },
  {
    segment: "catalog",
    icon: BookOpen,
    label: "Catalog",
    group: "Data",
  },
  {
    segment: "health",
    icon: HeartPulse,
    label: "Lakehouse health",
    group: "Data",
  },
  {
    segment: "dashboards",
    icon: LayoutDashboard,
    label: "Dashboards",
    group: "Analytics",
  },
  {
    segment: "semantic",
    icon: Ruler,
    label: "Semantic models",
    group: "Analytics",
  },
  // Not admin-gated: a per-agent grant entitles its holder to that agent's
  // status and monitoring page without any global permission, and the page
  // itself shows only the agents the server says they can see.
  {
    segment: "compute",
    icon: Microchip,
    label: "Compute",
    group: "Manage",
  },
  {
    segment: "admin",
    icon: Settings,
    label: "Admin",
    group: "Manage",
    requiresAdmin: true,
  },
  // Lives in the user menu (TopBar), not the navigation — workspace identity
  // and personal preferences aren't a destination someone browses to
  // repeatedly the way Worksheets or Catalog are.
  {
    segment: "settings",
    icon: SlidersHorizontal,
    label: "Settings",
    group: "Manage",
    hiddenFromRail: true,
  },
];

export const settingsItem = navItems.find(
  (item) => item.segment === "settings",
)!;
