import { useRouterState, useNavigate } from "@tanstack/react-router";
import {
  FileText,
  BookOpen,
  Database,
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

interface NavItem {
  segment: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  matchSegment: string;
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
    icon: Database,
    label: "Saved queries",
    matchSegment: "saved-queries",
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
  },
];

interface LeftRailProps {
  ws: string;
}

export function LeftRail({ ws }: LeftRailProps) {
  const state = useRouterState();
  const pathname = state.location.pathname;
  const navigate = useNavigate();

  return (
    <TooltipProvider delayDuration={400}>
      <nav
        className="flex flex-col items-center gap-1 py-2 w-12 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] relative"
        aria-label="Main navigation"
      >
        {navItems.map(({ segment, icon: Icon, label, matchSegment }) => {
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
