import { useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { cn } from "@/utils";

/**
 * A capped-height, scrollable view of a SQL statement with a button that
 * expands it in place to a taller box — no popup — so a long statement is
 * never silently ellipsized with no way to read it.
 */
export function SqlPreview({
  sql,
  maxHeightClassName = "max-h-32",
  expandedMaxHeightClassName = "max-h-[60vh]",
}: {
  sql: string;
  maxHeightClassName?: string;
  expandedMaxHeightClassName?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="relative min-w-0">
      <pre
        className={cn(
          "overflow-y-auto whitespace-pre-wrap break-words pr-5 font-mono text-xs text-text-primary",
          expanded ? expandedMaxHeightClassName : maxHeightClassName,
        )}
      >
        {sql}
      </pre>
      <button
        type="button"
        aria-label={expanded ? "Collapse SQL" : "Expand SQL"}
        onClick={() => setExpanded((v) => !v)}
        className="absolute right-0 top-0 rounded p-0.5 text-text-tertiary hover:text-text-primary"
      >
        {expanded ? (
          <Minimize2 className="size-3.5" />
        ) : (
          <Maximize2 className="size-3.5" />
        )}
      </button>
    </div>
  );
}
