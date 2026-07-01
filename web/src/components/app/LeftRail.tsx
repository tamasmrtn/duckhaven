import { useRouterState, useNavigate } from "@tanstack/react-router";
import {
  FileText,
  BookOpen,
  BookMarked,
  CalendarClock,
  Clock,
  Settings,
  HeartPulse,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/utils";
import { useMe } from "@/queries/auth";

interface NavItem {
  segment: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  matchSegment: string;
  // When true, the item is only shown to users holding at least one global
  // permission (the admin section is enforced server-side regardless).
  requiresAdmin?: boolean;
}

const navItems: NavItem[] = [
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
    segment: "admin/agents",
    icon: Settings,
    label: "Admin",
    matchSegment: "admin",
    requiresAdmin: true,
  },
];

interface LeftRailProps {
  ws: string;
}

export function LeftRail({ ws }: LeftRailProps) {
  const state = useRouterState();
  const pathname = state.location.pathname;
  const navigate = useNavigate();
  const { data: me } = useMe();
  const isAdmin = (me?.permissions?.length ?? 0) > 0;
  const items = navItems.filter((item) => !item.requiresAdmin || isAdmin);

  return (
    <TooltipProvider delayDuration={400}>
      <nav
        className="flex flex-col items-center gap-1 py-2 w-12 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] relative"
        aria-label="Main navigation"
      >
        {items.map(({ segment, icon: Icon, label, matchSegment }) => {
          const active = pathname.includes(`/${matchSegment}`);
          return (
            <Tooltip key={segment}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() =>
                    void navigate({ to: `/${ws}/${segment}` as "/" })
                  }
                  className={cn(
                    "relative flex size-9 items-center justify-center rounded-md text-text-secondary transition-colors",
                    "hover:bg-accent hover:text-text-primary",
                    "focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)] focus-visible:outline-offset-2",
                    active && "bg-accent text-text-primary",
                  )}
                  aria-label={label}
                  aria-current={active ? "page" : undefined}
                >
                  <Icon className="size-[18px]" />
                  {active && (
                    <span
                      className="absolute left-0 h-5 w-0.5 rounded-r bg-[var(--brand-yellow)]"
                      aria-hidden
                    />
                  )}
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" className="text-xs">
                {label}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </nav>
    </TooltipProvider>
  );
}
