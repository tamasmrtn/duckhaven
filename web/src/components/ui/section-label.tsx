import type { ReactNode } from "react";
import { cn } from "@/utils";

/**
 * The small-caps heading over a section of a page or panel ("Schema",
 * "Timeline", "Recommendations"). One look everywhere; `className` only places
 * it (margins, padding, truncation).
 *
 * An h2 by default: every page's title is an h1, so an h3 here would skip a
 * level. Pass `as` for a label nested under another one.
 */
export function SectionLabel({
  as: Tag = "h2",
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
