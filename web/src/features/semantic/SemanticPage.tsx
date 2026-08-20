import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Ruler, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError } from "@/api/client";
import { useSemanticModels } from "@/queries/semantic";
import { useCreateSemanticModel } from "@/queries/semantic.mutations";
import { StatusPill } from "./SemanticStatusPill";
import { plural } from "@/utils";

/**
 * The list of subject areas a workspace has curated.
 *
 * Kept to a table rather than cards: the interesting columns are status, how
 * much is in it, and whether anything is broken — all of which read better in a
 * row than in a tile.
 */
export function SemanticPage() {
  const { ws } = useParams({ from: "/$ws/semantic" });
  const navigate = useNavigate();
  const { data: models, isLoading } = useSemanticModels(ws);
  const create = useCreateSemanticModel(ws);

  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");

  const submit = () => {
    create.mutate(
      { slug, name: name || slug, description: null },
      {
        onSuccess: (model) => {
          setOpen(false);
          setSlug("");
          setName("");
          void navigate({
            to: "/$ws/semantic/$model",
            params: { ws, model: model.slug },
          });
        },
        onError: (error) => {
          toast.error(
            error instanceof ApiError
              ? error.message
              : "Could not create model",
          );
        },
      },
    );
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Semantic models"
        description="What this workspace's business terms mean, and how they are calculated. Published models are what the assistant answers from."
        actions={
          <Button
            size="sm"
            className="h-7 gap-1.5 text-xs"
            onClick={() => setOpen(true)}
          >
            <Plus className="size-3" />
            New model
          </Button>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto px-6 py-4">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : !models || models.length === 0 ? (
          <EmptyState
            icon={Ruler}
            title="No semantic models yet"
            description="Define what revenue, orders or customers mean here, and the assistant will use those definitions instead of working them out from column names."
            action={
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={() => setOpen(true)}
              >
                Create the first one
              </Button>
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Source</TableHead>
                <TableHead className="text-right">Metrics</TableHead>
                <TableHead className="text-right">Dimensions</TableHead>
                <TableHead>Health</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model) => (
                <TableRow
                  key={model.id}
                  className="cursor-pointer"
                  onClick={() =>
                    void navigate({
                      to: "/$ws/semantic/$model",
                      params: { ws, model: model.slug },
                    })
                  }
                >
                  <TableCell>
                    <div className="font-medium">{model.name}</div>
                    {model.description && (
                      <div className="text-xs text-text-tertiary">
                        {model.description}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusPill status={model.status} />
                  </TableCell>
                  <TableCell className="text-xs text-text-secondary">
                    {model.provider === "native"
                      ? "Defined here"
                      : model.provider}
                  </TableCell>
                  <TableCell className="text-right font-tabular">
                    {model.metric_count}
                  </TableCell>
                  <TableCell className="text-right font-tabular">
                    {model.dimension_count}
                  </TableCell>
                  <TableCell className="text-xs">
                    {model.broken_count > 0 ? (
                      <span className="text-[var(--status-failed)]">
                        {plural(model.broken_count, "definition")} broken
                      </span>
                    ) : (
                      <span className="text-text-tertiary">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New semantic model</DialogTitle>
            <DialogDescription>
              One subject area — sales, marketing, support. Keeping a model
              focused is what makes the assistant reliable with it; around ten
              tables is where accuracy starts to suffer.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label
                className="mb-1 block text-xs text-text-secondary"
                htmlFor="slug"
              >
                Identifier
              </label>
              <Input
                id="slug"
                value={slug}
                placeholder="sales"
                onChange={(e) => setSlug(e.target.value)}
              />
              <p className="mt-1 text-2xs text-text-tertiary">
                Lowercase letters, digits and underscores. Used in URLs and by
                the assistant.
              </p>
            </div>
            <div>
              <label
                className="mb-1 block text-xs text-text-secondary"
                htmlFor="name"
              >
                Display name
              </label>
              <Input
                id="name"
                value={name}
                placeholder="Sales"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!slug || create.isPending}
              onClick={submit}
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
