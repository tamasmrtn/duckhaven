import {
  ChevronDown,
  Search,
  Sun,
  Moon,
  Monitor,
  LogOut,
  User,
} from "lucide-react";
import logoLight from "@/assets/logo-light.svg";
import logoDark from "@/assets/logo-dark.svg";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useMe, useLogout } from "@/queries/auth";
import { useNavigate } from "@tanstack/react-router";
import { useTheme } from "@/hooks/useTheme";
import { StorageIcon } from "./StorageIcon";
import type { Workspace } from "@/types/workspace";

interface TopBarProps {
  workspace?: Workspace;
  onWorkspaceSwitcher: () => void;
  onCommandPalette: () => void;
}

export function TopBar({
  workspace,
  onWorkspaceSwitcher,
  onCommandPalette,
}: TopBarProps) {
  const { data: me } = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const { theme, setTheme } = useTheme();

  const themeIcons = {
    light: <Sun className="size-4" />,
    dark: <Moon className="size-4" />,
    system: <Monitor className="size-4" />,
  };

  return (
    <header className="flex h-12 items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 shrink-0">
      {/* Duck mark + workspace switcher */}
      <button
        type="button"
        onClick={onWorkspaceSwitcher}
        className="flex items-center gap-2 rounded-md px-2 py-1 text-sm font-semibold hover:bg-accent focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
        aria-label="Switch workspace"
      >
        <img
          src={logoLight}
          alt="DuckHaven"
          className="h-6 w-auto block dark:hidden"
        />
        <img
          src={logoDark}
          alt="DuckHaven"
          className="h-6 w-auto hidden dark:block"
        />
        {workspace && (
          <>
            <span className="font-medium">{workspace.name}</span>
            <StorageIcon
              kind={workspace.storage_backend_kind}
              className="size-3.5 text-text-secondary"
            />
          </>
        )}
        <ChevronDown className="size-3.5 text-text-secondary" />
      </button>

      {/* Command palette trigger */}
      <button
        type="button"
        onClick={onCommandPalette}
        className="ml-2 flex h-8 w-56 items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-3 text-sm text-text-tertiary hover:border-[var(--border-strong)] focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
        aria-label="Open command palette"
      >
        <Search className="size-3.5" />
        <span className="flex-1 text-left">Search…</span>
        <kbd className="hidden items-center gap-0.5 rounded bg-muted px-1 font-mono text-2xs md:flex">
          <span>⌘</span>K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-1">
        {/* Theme toggle */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label="Change theme"
            >
              {themeIcons[theme]}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-36">
            <DropdownMenuItem
              onClick={() => setTheme("light")}
              className="gap-2"
            >
              <Sun className="size-4" /> Light
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setTheme("dark")}
              className="gap-2"
            >
              <Moon className="size-4" /> Dark
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setTheme("system")}
              className="gap-2"
            >
              <Monitor className="size-4" /> System
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* User menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-2 px-2 text-sm"
            >
              <User className="size-4" />
              {me?.name ?? "Account"}
              <ChevronDown className="size-3 text-text-secondary" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <div className="px-2 py-1.5 text-xs text-text-secondary">
              {me?.email}
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="gap-2 text-[var(--status-failed)]"
              onClick={async () => {
                await logout.mutateAsync();
                void navigate({ to: "/login" });
              }}
            >
              <LogOut className="size-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
