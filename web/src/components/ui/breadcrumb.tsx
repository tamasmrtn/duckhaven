import * as React from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/utils";

export interface Crumb {
  label: string;
  // Render this segment as the emphasized (current/anchor) crumb.
  emphasis?: boolean;
}

// The catalog path trail (workspace › catalog › schema › table). Segments are
// plain labels, not links; which ones are emphasized varies per view.
export function Breadcrumb({
  items,
  className,
}: {
  items: Crumb[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 text-xs text-text-secondary",
        className,
      )}
    >
      {items.map((item, i) => (
        <React.Fragment key={i}>
          {i > 0 && <ChevronRight className="size-3" />}
          <span
            className={
              item.emphasis ? "font-medium text-text-primary" : undefined
            }
          >
            {item.label}
          </span>
        </React.Fragment>
      ))}
    </div>
  );
}
