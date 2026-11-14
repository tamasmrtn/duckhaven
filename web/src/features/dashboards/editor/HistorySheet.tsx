import { History } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboardRevisions } from "@/queries/dashboards";
import { relativeTime } from "@/utils/relativeTime";

/**
 * Every publish of the dashboard, newest first. Opening one puts its file in
 * the editor, to save as the draft and publish again.
 */
export function HistorySheet({
  ws,
  name,
  open,
  onOpenChange,
  onOpen,
}: {
  ws: string;
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpen: (revision: number) => void;
}) {
  const { data: revisions, isLoading } = useDashboardRevisions(ws, name, open);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Revisions</SheetTitle>
          <SheetDescription>
            Each time the dashboard was published. Open one to put its file in
            the editor; saving and publishing it restores it.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 space-y-2">
          {isLoading ? (
            <Skeleton className="h-14 w-full" />
          ) : !revisions?.length ? (
            <p className="text-xs text-text-tertiary">
              Nobody has published this dashboard.
            </p>
          ) : (
            <ol className="space-y-2">
              {revisions.map((r, i) => (
                <li
                  key={r.number}
                  className="flex items-start justify-between gap-3 rounded-md border border-[var(--border-subtle)] px-3 py-2"
                >
                  <div className="min-w-0 space-y-0.5">
                    <p className="text-xs font-medium text-text-primary">
                      Revision {r.number}
                      {i === 0 && (
                        <span className="font-normal text-text-tertiary">
                          {" "}
                          · what readers see
                        </span>
                      )}
                    </p>
                    <p className="text-2xs text-text-tertiary">
                      Published {relativeTime(r.published_at)}
                    </p>
                    {r.message && (
                      <p className="break-words text-xs text-text-secondary">
                        {r.message}
                      </p>
                    )}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 shrink-0 gap-1.5 text-xs"
                    onClick={() => onOpen(r.number)}
                    aria-label={`Open revision ${r.number} in the editor`}
                  >
                    <History className="size-3.5" aria-hidden />
                    Open
                  </Button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
