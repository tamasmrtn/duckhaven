import { useState } from "react";
import { Maximize2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/utils";

/**
 * A capped-height, scrollable view of a SQL statement with an expand button
 * that pops the full text into a dialog. Used wherever a query's SQL is shown
 * so a long statement is never silently ellipsized with no way to read it.
 */
export function SqlPreview({
  sql,
  maxHeightClassName = "max-h-32",
}: {
  sql: string;
  maxHeightClassName?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="group relative min-w-0">
      <pre
        className={cn(
          "overflow-y-auto whitespace-pre-wrap break-words pr-5 font-mono text-xs text-text-primary",
          maxHeightClassName,
        )}
      >
        {sql}
      </pre>
      <button
        type="button"
        aria-label="Expand SQL"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        className="absolute right-0 top-0 rounded p-0.5 text-text-tertiary opacity-0 hover:text-text-primary group-hover:opacity-100 focus-visible:opacity-100"
      >
        <Maximize2 className="size-3.5" />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-w-3xl"
          onClick={(e) => e.stopPropagation()}
        >
          <DialogHeader>
            <DialogTitle>Query</DialogTitle>
          </DialogHeader>
          <pre className="max-h-[70vh] overflow-auto whitespace-pre font-mono text-xs text-text-primary">
            {sql}
          </pre>
        </DialogContent>
      </Dialog>
    </div>
  );
}
