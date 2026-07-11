import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/utils";
import { pickVerb } from "./statusVerbs";

const ROTATE_EVERY_MS = 2000;

/** Fills the dead air between Send and the first token/tool call, and while a
 * tool call is in flight, with a rotating whimsical status word and an elapsed
 * timer — instead of a blank panel or a static "Running tool…" line.
 *
 * Only rendered by the caller while a turn is streaming, so mounting starts
 * the clock at 0 and unmounting clears it — no reset-on-prop-change effect. */
export function ThinkingStatus({
  currentTool,
}: {
  currentTool: string | null;
}) {
  const prefersReducedMotion = useMediaQuery(
    "(prefers-reduced-motion: reduce)",
  );
  const [tick, setTick] = useState(0);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const interval = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (prefersReducedMotion) return;
    const interval = setInterval(() => {
      setTick((t) => t + 1);
    }, ROTATE_EVERY_MS);
    return () => clearInterval(interval);
  }, [prefersReducedMotion]);

  return (
    <p
      className="flex items-center gap-1.5 text-xs text-text-secondary"
      role="status"
    >
      <RefreshCw
        className={cn("size-3", !prefersReducedMotion && "animate-spin")}
        aria-hidden="true"
      />
      {pickVerb(currentTool, tick)}{" "}
      <span className="tabular-nums">{elapsedSeconds}s</span>
    </p>
  );
}
