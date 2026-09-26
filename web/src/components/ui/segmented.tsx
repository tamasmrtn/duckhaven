import { cn } from "@/utils";

// The one look for every set of options and tabs in the app (see tabs.tsx).
// `md` is the page-level size, the height of the buttons and inputs beside it;
// `sm` fits inside an editor pane's header row.
export const SEGMENT_SIZES = { md: "h-8", sm: "h-7" } as const;
export const segmentGroupClass =
  "inline-flex items-center gap-0.5 rounded-md border border-[var(--border-subtle)] p-0.5";
export const segmentItemClass =
  "inline-flex h-full items-center justify-center whitespace-nowrap rounded px-2.5 text-xs text-text-secondary transition-colors hover:text-text-primary";
export const segmentActiveClass = "bg-accent font-medium text-text-primary";

/**
 * A small row of mutually exclusive options.
 *
 * Promoted here from LineagePanel, which had the only parameterized copy of a
 * control that had been hand-rolled three times. `aria-pressed` rather than
 * radio semantics because these are toggles over a view, not a form field.
 *
 * `label` doubles as the group's accessible name; pass `hideLabel` where the
 * surrounding UI already says what the group is for, so the name is still there
 * for assistive tech without repeating it on screen.
 */
export function Segmented<T extends string | number>({
  label,
  hideLabel = false,
  options,
  value,
  onChange,
  size = "md",
  className,
}: {
  label: string;
  hideLabel?: boolean;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  size?: keyof typeof SEGMENT_SIZES;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      {!hideLabel && (
        <span className="text-2xs uppercase tracking-wide text-text-tertiary">
          {label}
        </span>
      )}
      <div
        role="group"
        aria-label={label}
        className={cn(segmentGroupClass, SEGMENT_SIZES[size])}
      >
        {options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            className={cn(
              segmentItemClass,
              option.value === value && segmentActiveClass,
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
