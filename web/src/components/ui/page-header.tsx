import type { ReactNode } from "react";
import { cn } from "@/utils";

export interface PageHeaderProps {
  title: ReactNode;
  // Rendered above the title row (e.g. workspace › section › object).
  breadcrumb?: ReactNode;
  // Rendered inline before the title (e.g. a back button).
  leading?: ReactNode;
  // Rendered inline after the title (e.g. a status pill).
  badge?: ReactNode;
  // Subtitle below the title row.
  description?: ReactNode;
  // Right-aligned, same row as the title.
  actions?: ReactNode;
  // A full extra row below everything else (e.g. section nav tabs).
  secondaryRow?: ReactNode;
  className?: string;
}

/**
 * The bordered title bar shared by every top-level page: a title (with
 * optional breadcrumb, leading icon/button, and trailing badge), an optional
 * description, right-aligned actions, and an optional extra row underneath.
 */
export function PageHeader({
  title,
  breadcrumb,
  leading,
  badge,
  description,
  actions,
  secondaryRow,
  className,
}: PageHeaderProps) {
  // A breadcrumb or description forces the title onto its own row inside a
  // left-hand column, so it can stack above/below them; otherwise leading,
  // title and badge sit directly in the header row as plain flex siblings.
  const hasLeftColumn = Boolean(breadcrumb) || Boolean(description);
  const titleRow = (
    <>
      {leading}
      <h1 className="text-md font-semibold">{title}</h1>
      {badge}
    </>
  );

  return (
    <div
      className={cn(
        "border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0",
        className,
      )}
    >
      <div
        className={cn(
          "flex gap-3",
          breadcrumb ? "items-start" : "items-center",
          actions && "justify-between",
        )}
      >
        {hasLeftColumn ? (
          <div className="min-w-0">
            {breadcrumb}
            <div
              className={cn("flex items-center gap-2", breadcrumb && "mt-1")}
            >
              {titleRow}
            </div>
            {description && (
              <p className="mt-0.5 text-xs text-text-tertiary">{description}</p>
            )}
          </div>
        ) : (
          titleRow
        )}
        {actions}
      </div>
      {secondaryRow}
    </div>
  );
}
