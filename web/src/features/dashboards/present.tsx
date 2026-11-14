import { useEffect } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { REFRESH_MINUTES } from "./params";

const OFF = "off";

function every(minutes: number): string {
  return minutes === 60 ? "Every hour" : `Every ${minutes} minutes`;
}

/**
 * How often present mode runs the dashboard again. Choosing says what that
 * costs: a refresh runs every widget whose tables changed, so data that keeps
 * changing keeps compute from stopping.
 */
export function RefreshEvery({
  minutes,
  onChange,
}: {
  minutes: number | undefined;
  onChange: (minutes: number | undefined) => void;
}) {
  return (
    <Select
      value={minutes ? String(minutes) : OFF}
      onValueChange={(v) => onChange(v === OFF ? undefined : Number(v))}
    >
      <SelectTrigger
        aria-label="Refresh every"
        className="h-7 w-auto gap-1.5 text-xs"
      >
        <span className="text-text-tertiary">Refresh:</span>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={OFF} className="text-xs">
          Off
        </SelectItem>
        {REFRESH_MINUTES.map((m) => (
          <SelectItem key={m} value={String(m)} className="text-xs">
            {every(m)}
          </SelectItem>
        ))}
        <SelectSeparator />
        <p className="max-w-64 px-2 py-1.5 text-2xs text-text-tertiary">
          Each refresh runs the widgets whose tables changed since the last one,
          and the cache answers the rest. While the data keeps changing,
          refreshing keeps compute awake.
        </p>
      </SelectContent>
    </Select>
  );
}

/**
 * Calls `refresh` every `minutes`, skipping while the tab is hidden: nobody
 * would see the answers, so nothing should run for them.
 */
export function useAutoRefresh(
  minutes: number | undefined,
  refresh: () => void,
): void {
  useEffect(() => {
    if (!minutes) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") refresh();
    }, minutes * 60_000);
    return () => window.clearInterval(timer);
  }, [minutes, refresh]);
}

/** Fills the screen when the browser allows it; present mode works either way. */
export function enterFullscreen(): void {
  document.documentElement.requestFullscreen?.()?.catch(() => undefined);
}

export function exitFullscreen(): void {
  if (document.fullscreenElement) {
    document.exitFullscreen?.()?.catch(() => undefined);
  }
}
