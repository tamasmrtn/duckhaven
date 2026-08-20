import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}

/** Shared empty-state placeholder: centered icon + message (+ optional action). */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <Icon className="size-8 text-text-tertiary" />
      <p className="text-md font-medium text-text-secondary">{title}</p>
      {description && (
        <p className="text-sm text-text-tertiary">{description}</p>
      )}
      {action}
    </div>
  );
}
