import { useState, useEffect } from "react";
import { Outlet, useParams } from "@tanstack/react-router";
import { useWorkspace } from "@/queries/workspaces";
import { TopBar } from "./TopBar";
import { LeftRail } from "./LeftRail";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { CommandPalette } from "./CommandPalette";

export function AppShell() {
  const { ws } = useParams({ from: "/$ws" });
  const { data: workspace } = useWorkspace(ws);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

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
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

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
