import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Shown when another window saved this worksheet after this one loaded it.
 * Autosave is paused until the user picks a side, so neither edit is lost by
 * accident.
 */
export function ConflictBanner({
  onLoadTheirs,
  onKeepMine,
}: {
  onLoadTheirs: () => void;
  onKeepMine: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[color-mix(in_oklab,var(--status-running)_12%,var(--bg-surface))] px-3 py-1.5 text-xs shrink-0"
    >
      <AlertTriangle className="size-3.5 shrink-0 text-[var(--status-running)]" />
      <span className="text-text-primary">
        This worksheet was changed in another window. Your edits here are not
        saved yet.
      </span>
      <div className="ml-auto flex items-center gap-1">
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-xs"
          onClick={onLoadTheirs}
        >
          Load latest
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          onClick={onKeepMine}
        >
          Keep mine
        </Button>
      </div>
    </div>
  );
}
