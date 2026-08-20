import { useState, useEffect, useCallback, useRef } from "react";
import { Outlet, useParams } from "@tanstack/react-router";
import { useWorkspace } from "@/queries/workspaces";
import {
  AssistantProvider,
  useAssistant,
} from "@/features/assistant/AssistantContext";
import { AssistantPanel } from "@/features/assistant/AssistantPanel";
import { ThemeProvider } from "@/hooks/useTheme";
import { TopBar } from "./TopBar";
import { LeftRail } from "./LeftRail";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { CommandPalette } from "./CommandPalette";

export function AppShell() {
  return (
    <ThemeProvider>
      <AssistantProvider>
        <AppShellInner />
      </AssistantProvider>
    </ThemeProvider>
  );
}

function AppShellInner() {
  const { ws } = useParams({ from: "/$ws" });
  const { data: workspace } = useWorkspace(ws);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { open: assistantOpen, toggle: toggleAssistant } = useAssistant();
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
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggleAssistant]);

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
      <TopBar
        workspace={workspace}
        onWorkspaceSwitcher={() => setSwitcherOpen(true)}
        onCommandPalette={() => setPaletteOpen(true)}
      />
      <div className="flex flex-1 overflow-hidden">
        <LeftRail ws={ws} />
        <main className="flex flex-1 flex-col overflow-hidden">
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
