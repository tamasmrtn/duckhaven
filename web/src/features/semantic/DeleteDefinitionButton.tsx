import { useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { ApiError } from "@/api/client";
import {
  useDeleteDefinition,
  type DefinitionKind,
} from "@/queries/semantic.mutations";

/**
 * Remove one definition, with the consequence stated before the click.
 *
 * The confirm is a plain yes/no rather than the type-the-name gate the catalog's
 * drop dialog uses: dropping a table destroys data, whereas removing a metric
 * removes an agreement about data and the underlying table is untouched. The
 * ceremony should match the damage.
 *
 * The dataset case can be refused by the server with a 409 naming what still
 * binds it. That message is surfaced verbatim, because it is a list of the exact
 * things the caller has to remove first — advice this component cannot invent.
 */
export function DeleteDefinitionButton({
  ws,
  slug,
  kind,
  name,
  consequence,
}: {
  ws: string;
  slug: string;
  kind: DefinitionKind;
  name: string;
  /** What else changes when this goes, when anything does. */
  consequence?: string;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = useDeleteDefinition(ws, slug);
  const noun = kind.slice(0, -1);

  const confirm = () => {
    setError(null);
    remove.mutate(
      { kind, name },
      {
        onSuccess: () => {
          setOpen(false);
          toast.success(`Removed ${name}.`);
        },
        onError: (err) => {
          // `ApiError.message` is already the unwrapped `detail.detail`, which
          // for the dataset refusal is the list of dependents.
          setError(
            err instanceof ApiError || err instanceof Error
              ? err.message
              : `Could not remove ${name}.`,
          );
        },
      },
    );
  };

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="size-7 text-text-tertiary hover:text-[var(--status-error)]"
        aria-label={`Remove ${noun} ${name}`}
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        <Trash2 className="size-3.5" />
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {noun}?</DialogTitle>
            <DialogDescription>
              <code className="font-mono">{name}</code>
              {consequence ? ` — ${consequence}` : "."} The table it reads is
              not touched.
            </DialogDescription>
          </DialogHeader>

          {error && <Banner>{error}</Banner>}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirm}
              disabled={remove.isPending}
            >
              {remove.isPending ? "Removing…" : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
