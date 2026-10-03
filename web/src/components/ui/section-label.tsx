import type { ReactNode } from "react";
import { cn } from "@/utils";

/**
 * The small-caps heading over a section of a page or panel ("Schema",
 * "Timeline", "Recommendations"). One look everywhere; `className` only places
 * it (margins, padding, truncation).
 */
export function SectionLabel({
  as: Tag = "h3",
  className,
  children,
}: {
  as?: "h2" | "h3" | "h4";
  className?: string;
  children: ReactNode;
}) {
  return (
    <Tag
      className={cn(
        "text-xs font-semibold uppercase tracking-wide text-text-secondary",
        className,
      )}
    >
      {children}
    </Tag>
  );
}
