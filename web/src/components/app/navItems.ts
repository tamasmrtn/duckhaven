import {
  FileText,
  BookOpen,
  BookMarked,
  CalendarClock,
  Clock,
  Microchip,
  Plug,
  Settings,
  SlidersHorizontal,
  HeartPulse,
  Ruler,
} from "lucide-react";

export interface NavItem {
  segment: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  matchSegment: string;
  // When true, the item is only shown to users holding at least one global
  // permission (the admin section is enforced server-side regardless).
  requiresAdmin?: boolean;
  // When true, the item is a real destination (surfaced in the command
  // palette) but not shown in the icon-only rail — e.g. Settings, which
  // lives in the user menu instead.
  hiddenFromRail?: boolean;
}

export const navItems: NavItem[] = [
  {
    segment: "worksheets",
    icon: FileText,
    label: "Worksheets",
    matchSegment: "worksheets",
  },
  {
    segment: "catalog",
    icon: BookOpen,
    label: "Catalog",
    matchSegment: "catalog",
  },
  {
    segment: "saved-queries",
    icon: BookMarked,
    label: "Saved queries",
    matchSegment: "saved-queries",
  },
  {
    segment: "schedules",
    icon: CalendarClock,
    label: "Schedules",
    matchSegment: "schedules",
  },
  {
    segment: "semantic",
    icon: Ruler,
    label: "Semantic models",
    matchSegment: "semantic",
  },
  {
    segment: "sessions",
    icon: Plug,
    label: "Connections",
    matchSegment: "sessions",
  },
  // Not admin-gated: a per-agent grant entitles its holder to that agent's
  // status and monitoring page without any global permission, and the page
  // itself shows only the agents the server says they can see.
  {
    segment: "compute",
    icon: Microchip,
    label: "Compute",
    matchSegment: "compute",
  },
  {
    segment: "history",
    icon: Clock,
    label: "History",
    matchSegment: "history",
  },
  {
    segment: "health",
    icon: HeartPulse,
    label: "Lakehouse health",
    matchSegment: "health",
  },
  {
    segment: "admin",
    icon: Settings,
    label: "Admin",
    matchSegment: "admin",
    requiresAdmin: true,
  },
  // Lives in the user menu (TopBar), not the rail — workspace identity and
  // personal preferences aren't a destination someone browses to repeatedly
  // the way Worksheets or Catalog are.
  {
    segment: "settings",
    icon: SlidersHorizontal,
    label: "Settings",
    matchSegment: "settings",
    hiddenFromRail: true,
  },
];
