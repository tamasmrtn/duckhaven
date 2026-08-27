import { useEffect, useRef, useState } from "react";

/**
 * Throttles a fast-changing string to at most one update per `delayMs`. Unlike
 * `useDebouncedValue`, it emits on a leading edge and keeps emitting while the
 * value keeps changing, so it still updates during a continuous stream of
 * changes rather than waiting for one to stop.
 */
export function useThrottledText(value: string, delayMs: number): string {
  const [throttled, setThrottled] = useState(value);
  const lastFiredAtRef = useRef(0);
  useEffect(() => {
    const elapsed = Date.now() - lastFiredAtRef.current;
    if (elapsed >= delayMs) {
      lastFiredAtRef.current = Date.now();
      setThrottled(value);
      return;
    }
    const timeout = setTimeout(() => {
      lastFiredAtRef.current = Date.now();
      setThrottled(value);
    }, delayMs - elapsed);
    return () => clearTimeout(timeout);
  }, [value, delayMs]);
  return throttled;
}
