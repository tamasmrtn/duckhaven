import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useNavMode } from "@/hooks/useNavMode";
import { useMe } from "@/queries/auth";
import { cn } from "@/utils";
import { navGroups, navItems } from "./navItems";

// Pointer rest before expand-on-hover opens, and grace before it closes: a
// quick pass across the rail on the way to the page never opens it.
const OPEN_DELAY_MS = 200;
const CLOSE_DELAY_MS = 300;

/**
 * The main navigation on desktop: a labelled column in four groups, or the
 * 48px icon rail, or the rail that opens over the page on hover — whichever
 * the user chose (see useNavMode).
 */
export function MainNav({ ws }: { ws: string }) {
  const { mode } = useNavMode();
  const hover = mode === "hover";

  const [pointerOpen, setPointerOpen] = useState(false);
  const [focusOpen, setFocusOpen] = useState(false);
  // The pending open or close; undefined when none is.
  const timer = useRef<number | undefined>(undefined);
  // Set between pointerdown and the focus it causes, so a click doesn't count
  // as keyboard focus and pin the panel open.
  const pointerDown = useRef(false);
  // Set by Escape and cleared when the pointer leaves: a dismissed panel stays
  // closed under a pointer that is still on the rail (WCAG 1.4.13).
  const dismissed = useRef(false);

  if (!hover && (pointerOpen || focusOpen)) {
    setPointerOpen(false);
    setFocusOpen(false);
  }
  const open = hover && (pointerOpen || focusOpen);

  const schedule = (fn: () => void, ms: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = undefined;
      fn();
    }, ms);
  };
  const close = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    setPointerOpen(false);
    setFocusOpen(false);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // Escape closes it wherever focus is, the editor included (WCAG 1.4.13).
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      dismissed.current = true;
      close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  const hoverHandlers = hover
    ? {
        onPointerEnter: (e: React.PointerEvent) => {
          if (e.pointerType !== "touch")
            schedule(() => setPointerOpen(true), OPEN_DELAY_MS);
        },
        onPointerLeave: (e: React.PointerEvent) => {
          dismissed.current = false;
          if (e.pointerType !== "touch")
            schedule(() => setPointerOpen(false), CLOSE_DELAY_MS);
        },
        // Choosing a page closes the panel with the pointer still on the rail;
        // moving it there opens the panel again, after the same delay.
        onPointerMove: (e: React.PointerEvent) => {
          if (
            e.pointerType !== "touch" &&
            !open &&
            timer.current === undefined &&
            !dismissed.current
          )
            schedule(() => setPointerOpen(true), OPEN_DELAY_MS);
        },
        onPointerDown: () => {
          pointerDown.current = true;
        },
        onPointerUp: () => {
          pointerDown.current = false;
        },
        onFocus: () => {
          if (pointerDown.current) pointerDown.current = false;
          else setFocusOpen(true);
        },
        onBlur: (e: React.FocusEvent) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null))
            setFocusOpen(false);
        },
      }
    : {};

  return (
    // Holds the column's place in the layout; in hover mode the panel opens
    // over the page from here, so the page never moves.
    <div
      className={cn(
        "relative shrink-0",
        mode === "expanded" ? "w-[200px]" : "w-12",
      )}
    >
      <nav
        id="main-navigation"
        aria-label="Main navigation"
        data-mode={mode}
        data-open={open || undefined}
        className={cn(
          "flex h-full flex-col overflow-y-auto overflow-x-hidden border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 py-2",
          hover &&
            "absolute inset-y-0 left-0 z-30 transition-[width] duration-150 ease-out motion-reduce:transition-none",
          hover && (open ? "w-[200px] shadow-[var(--shadow-e3)]" : "w-12"),
        )}
        {...hoverHandlers}
      >
        <NavGroups
          ws={ws}
          labels={mode === "expanded" || open}
          tooltips={mode === "collapsed"}
          onNavigate={close}
        />
      </nav>
    </div>
  );
}

/**
 * The groups and their links, shared by the desktop navigation and the phone
 * drawer. Collapsed and expanded take the same rows and slots, so the link
 * under the pointer stays put when expand-on-hover opens.
 */
function NavGroups({
  ws,
  labels,
  tooltips,
  onNavigate,
}: {
  ws: string;
  labels: boolean;
  tooltips: boolean;
  onNavigate: () => void;
}) {
  const current = useRouterState({
    select: (s) => s.location.pathname.split("/")[2],
  });
  const { data: me } = useMe();
  const isAdmin = (me?.permissions?.length ?? 0) > 0;

  // The open tooltip, controlled so only the collapsed rail shows one: the
  // labels are on screen otherwise.
  const [tip, setTip] = useState<string | null>(null);
  if (!tooltips && tip !== null) setTip(null);

  return (
    <TooltipProvider delayDuration={400}>
      {navGroups.map((group, index) => {
        const items = navItems.filter(
          (item) =>
            item.group === group &&
            !item.hiddenFromRail &&
            (!item.requiresAdmin || isAdmin),
        );
        if (items.length === 0) return null;
        return (
          <div key={group} role="group" aria-label={group}>
            {/* A name when labels show, a divider when they don't. Not a
                heading: four of them would come before every page's h1. */}
            <div aria-hidden className="flex h-6 items-center px-2">
              {labels ? (
                <SectionLabel as="div" className="truncate">
                  {group}
                </SectionLabel>
              ) : (
                index > 0 && (
                  <div className="h-px w-full bg-[var(--border-subtle)]" />
                )
              )}
            </div>
            <ul className="flex flex-col gap-0.5">
              {items.map(({ segment, icon: Icon, label, alsoActiveOn }) => {
                // The route's own segment (/$ws/<segment>/…), not any
                // substring: "/catalog" also occurs in /admin/catalog-access.
                const active =
                  current === segment || !!alsoActiveOn?.includes(current);
                return (
                  <li key={segment}>
                    <Tooltip
                      open={tooltips && tip === segment}
                      onOpenChange={(isOpen) => {
                        if (!tooltips) return;
                        setTip((t) =>
                          isOpen ? segment : t === segment ? null : t,
                        );
                      }}
                    >
                      <TooltipTrigger asChild>
                        <Link
                          to={`/$ws/${segment}`}
                          params={{ ws }}
                          onClick={onNavigate}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "relative flex h-7 items-center gap-2 rounded-md px-2 text-sm text-text-secondary transition-colors",
                            "hover:bg-accent hover:text-text-primary",
                            "focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)] focus-visible:outline-offset-2",
                            active && "bg-accent text-text-primary",
                          )}
                        >
                          {active && (
                            <span
                              className="absolute -left-2 h-5 w-0.5 rounded-r bg-[var(--brand-yellow)]"
                              aria-hidden
                            />
                          )}
                          <Icon className="size-4 shrink-0" />
                          <span
                            className={cn(
                              "whitespace-nowrap",
                              labels ? "truncate" : "sr-only",
                            )}
                          >
                            {label}
                          </span>
                        </Link>
                      </TooltipTrigger>
                      <TooltipContent side="right" className="text-xs">
                        {label}
                      </TooltipContent>
                    </Tooltip>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </TooltipProvider>
  );
}

/** The top bar's button that expands and collapses the navigation (⌘B). */
export function NavToggle() {
  const { mode, toggle } = useNavMode();
  const expanded = mode === "expanded";
  return (
    <TooltipProvider delayDuration={400}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            aria-label="Toggle navigation"
            aria-expanded={expanded}
            aria-controls="main-navigation"
            aria-keyshortcuts="Meta+B Control+B"
            onClick={toggle}
          >
            {expanded ? (
              <PanelLeftClose className="size-4" />
            ) : (
              <PanelLeftOpen className="size-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent
          side="bottom"
          className="flex items-center gap-2 text-xs"
        >
          {expanded ? "Collapse navigation" : "Expand navigation"}
          <kbd className="rounded bg-muted px-1 font-mono text-2xs">⌘B</kbd>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * The navigation on a phone: a drawer from the left, opened from the top bar
 * and closed by choosing a page. The saved desktop layout is left alone.
 */
export function MobileNav({ ws }: { ws: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          aria-label="Open navigation"
        >
          <Menu className="size-4" />
        </Button>
      </SheetTrigger>
      <SheetContent
        side="left"
        aria-describedby={undefined}
        className="flex w-64 flex-col gap-0 bg-[var(--bg-surface)] p-0"
      >
        <SheetHeader className="border-b border-[var(--border-subtle)] px-4 py-3">
          <SheetTitle className="text-sm">Navigation</SheetTitle>
        </SheetHeader>
        <nav
          aria-label="Main navigation"
          className="flex-1 overflow-y-auto px-2 py-2"
        >
          <NavGroups
            ws={ws}
            labels
            tooltips={false}
            onNavigate={() => setOpen(false)}
          />
        </nav>
      </SheetContent>
    </Sheet>
  );
}
