import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/utils";

const RESET_AFTER_MS = 2000;

/** Copies `text` to the clipboard and says whether it worked, aloud as well as by
 * icon: a screen reader hears the outcome from a status that stays mounted, so the
 * change is announced. Where the browser will not give a page the clipboard (an
 * insecure origin, a denied permission) it says so instead of looking like it
 * copied. */
export function CopyButton({
  text,
  label,
  className,
}: {
  text: string;
  /** What the button's accessible name says it copies, e.g. "Copy code". */
  label: string;
  className?: string;
}) {
  const [outcome, setOutcome] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setOutcome("copied");
    } catch {
      setOutcome("failed");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOutcome("idle"), RESET_AFTER_MS);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={label}
        title={label}
        className={cn(
          "rounded p-1 text-text-secondary hover:bg-[var(--bg-elevated)] hover:text-text-primary focus-visible:ring-1 focus-visible:ring-[var(--brand-slate-blue)]",
          className,
        )}
      >
        {outcome === "copied" ? (
          <Check className="size-3.5" aria-hidden="true" />
        ) : (
          <Copy className="size-3.5" aria-hidden="true" />
        )}
      </button>
      <span role="status" className="sr-only">
        {outcome === "copied"
          ? "Copied"
          : outcome === "failed"
            ? "Couldn't copy"
            : ""}
      </span>
    </>
  );
}
