import { useState, useEffect, useCallback, useRef } from "react";
import { Outlet, useParams } from "@tanstack/react-router";
import { useWorkspace } from "@/queries/workspaces";
import {
  AssistantProvider,
  useAssistant,
} from "@/features/assistant/AssistantContext";
import { AssistantPanel } from "@/features/assistant/AssistantPanel";
import { ThemeProvider } from "@/hooks/useTheme";
import { NavModeProvider, useNavMode } from "@/hooks/useNavMode";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { TopBar } from "./TopBar";
import { MainNav, MobileNav, NavToggle } from "./MainNav";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { CommandPalette } from "./CommandPalette";

export function AppShell() {
  return (
    <ThemeProvider>
      <NavModeProvider>
        <AssistantProvider>
          <AppShellInner />
        </AssistantProvider>
      </NavModeProvider>
    </ThemeProvider>
  );
}

function AppShellInner() {
  const { ws } = useParams({ from: "/$ws" });
  const { data: workspace } = useWorkspace(ws);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const { open: assistantOpen, toggle: toggleAssistant } = useAssistant();
  const { toggle: toggleNav } = useNavMode();
  // Below 768px the navigation is a drawer, and the saved layout waits.
  const isMobile = useMediaQuery("(max-width: 767px)");
  const [panelWidth, setPanelWidth] = useState(400);
  const draggingPanel = useRef(false);

  // Global keyboard shortcuts
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
      if (mod && e.key === ".") {
        e.preventDefault();
        setSwitcherOpen((v) => !v);
      }
      // Cmd/Ctrl+I toggles the assistant (matches Copilot's chat shortcut spirit).
      if (mod && (e.key === "i" || e.key === "I")) {
        e.preventDefault();
        toggleAssistant();
      }
      // Cmd/Ctrl+B expands or collapses the navigation, as in most editors.
      if (
        mod &&
        !e.altKey &&
        !e.shiftKey &&
        e.key.toLowerCase() === "b" &&
        !isMobile
      ) {
        e.preventDefault();
        if (!e.repeat) toggleNav();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggleAssistant, toggleNav, isMobile]);

  const onPanelDragStart = useCallback(
    (e: React.MouseEvent) => {
      draggingPanel.current = true;
      const startX = e.clientX;
      const startW = panelWidth;
      const onMove = (ev: MouseEvent) => {
        if (!draggingPanel.current) return;
        // Drag left edge: growing left = wider.
        setPanelWidth(
          Math.max(320, Math.min(720, startW + (startX - ev.clientX))),
        );
      };
      const onUp = () => {
        draggingPanel.current = false;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [panelWidth],
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[var(--bg-canvas)]">
      {/* First tab stop: skips the top bar's and navigation's ~16 controls.
          A plain anchor would add a history entry under the router, so focus
          the landmark directly. */}
      <a
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          mainRef.current?.focus();
        }}
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:border focus:border-[var(--border-strong)] focus:bg-[var(--bg-elevated)] focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:shadow-[var(--shadow-e2)]"
      >
        Skip to main content
      </a>
      <TopBar
        workspace={workspace}
        onWorkspaceSwitcher={() => setSwitcherOpen(true)}
        onCommandPalette={() => setPaletteOpen(true)}
        leading={isMobile ? <MobileNav ws={ws} /> : <NavToggle />}
      />
      <div className="flex flex-1 overflow-hidden">
        {!isMobile && <MainNav ws={ws} />}
        {/* isolate: the page's own layers (the editor's widgets reach z-50)
            stay below the navigation when it opens over the page. */}
        <main
          id="main-content"
          ref={mainRef}
          tabIndex={-1}
          className="isolate flex flex-1 flex-col overflow-hidden focus:outline-none"
        >
          <Outlet />
        </main>

        {assistantOpen && (
          <>
            <div
              onMouseDown={onPanelDragStart}
              className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-[var(--border-strong)]"
              aria-hidden
            />
            <aside
              className="flex shrink-0 flex-col overflow-hidden border-l border-[var(--border-subtle)] bg-[var(--bg-canvas)]"
              style={{ width: panelWidth }}
              aria-label="AI assistant"
            >
              <AssistantPanel ws={ws} />
            </aside>
          </>
        )}
      </div>

      <WorkspaceSwitcher
        open={switcherOpen}
        onClose={() => setSwitcherOpen(false)}
        currentWs={ws}
      />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        currentWs={ws}
      />
    </div>
  );
}
