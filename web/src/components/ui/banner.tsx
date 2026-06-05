import type { ReactNode } from "react";
import { cn } from "@/utils";

/**
 * A small, non-modal advisory note (orange accent). Used for the DR warning
 * on object_store backends (G-D18-c). Conveys meaning by label, not color alone.
 */
export function Banner({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="note"
      className={cn(
        "flex items-center gap-2 rounded-md border border-[var(--brand-orange)]/40 bg-[var(--brand-orange)]/10 px-3 py-2 text-xs text-text-secondary",
        className,
      )}
    >
      {children}
    </div>
  );
}
