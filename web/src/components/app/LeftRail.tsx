import { useRouterState, useNavigate } from "@tanstack/react-router";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/utils";
import { useMe } from "@/queries/auth";
import { navItems } from "./navItems";

interface LeftRailProps {
  ws: string;
}

export function LeftRail({ ws }: LeftRailProps) {
  const state = useRouterState();
  const pathname = state.location.pathname;
  const navigate = useNavigate();
  const { data: me } = useMe();
  const isAdmin = (me?.permissions?.length ?? 0) > 0;
  const items = navItems.filter(
    (item) => !item.hiddenFromRail && (!item.requiresAdmin || isAdmin),
  );

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
