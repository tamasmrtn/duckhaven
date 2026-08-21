import { cn } from "@/utils";

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
  className,
}: {
  label: string;
  hideLabel?: boolean;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
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
        className="flex rounded-md border border-[var(--border-subtle)] p-0.5"
      >
        {options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded px-2 py-0.5 text-2xs transition-colors",
              option.value === value
                ? "bg-accent text-text-primary font-medium"
                : "text-text-secondary hover:text-text-primary",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
