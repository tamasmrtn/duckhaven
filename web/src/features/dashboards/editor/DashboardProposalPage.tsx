import { useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, FilePen, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { dashboardsApi } from "@/api/dashboards";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { describeChange } from "@/features/assistant/ChatProposal";
import { useDashboardProposal } from "@/queries/dashboards";
import type { DashboardProposal } from "@/types/dashboard";
import { EditorPreview } from "./EditorPreview";
import { useLiveFile } from "./useLiveFile";

const GONE =
  "A proposal lasts a day, and only the person it was proposed to can open it. Ask the assistant again.";

/** What the page says about a proposal that can no longer be accepted, or was. */
function decided(p: DashboardProposal): string | null {
  switch (p.status) {
    case "accepted":
      return "You accepted these changes, so they are in the dashboard's draft.";
    case "discarded":
      return "You discarded these changes. The dashboard is as it was.";
    case "stale":
      return "The dashboard changed after these changes were proposed, and accepting them would undo that. Ask the assistant again.";
    case "expired":
      return "These changes expired a day after they were proposed. Ask the assistant again.";
    default:
      return null;
  }
}

/**
 * Changes the assistant proposed to a dashboard, for the person it proposed
 * them to. The page lists them and runs the file they make, as that person.
 * Accepting saves the file as the dashboard's draft, which an owner then
 * publishes; a dashboard managed in git takes the file in its repository.
 */
export function DashboardProposalPage() {
  const { ws, proposal: id } = useParams({
    from: "/$ws/dashboard-proposals/$proposal",
  });
  const { data, isLoading, error } = useDashboardProposal(ws, id);

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (!data) {
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <EmptyState
        icon={FilePen}
        title={
          missing ? "These changes are gone" : "These changes could not be read"
        }
        description={
          missing ? GONE : error instanceof Error ? error.message : undefined
        }
        action={
          <Link
            to="/$ws/dashboards"
            params={{ ws }}
            className="text-xs text-[var(--brand-slate-blue)] underline"
          >
            Every dashboard
          </Link>
        }
      />
    );
  }
  return <Review ws={ws} proposal={data} />;
}

function Review({ ws, proposal }: { ws: string; proposal: DashboardProposal }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const live = useLiveFile(ws, proposal.result_source);
  const [failure, setFailure] = useState<string | null>(null);
  const pending = proposal.status === "pending";
  const note = decided(proposal);

  const refresh = () =>
    queryClient.invalidateQueries({
      queryKey: ["workspace", ws, "dashboard-proposal", proposal.id],
    });

  const accept = useMutation({
    mutationFn: () => dashboardsApi.acceptProposal(ws, proposal.id),
    onSuccess: (draft) => {
      queryClient.setQueryData(
        ["workspace", ws, "dashboard", draft.name, "draft"],
        draft,
      );
      void queryClient.invalidateQueries({
        queryKey: ["workspace", ws, "dashboards"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["workspace", ws, "dashboard", draft.name],
        exact: true,
      });
      void refresh();
      toast.success("Saved as the draft");
      void navigate({
        to: "/$ws/dashboards/$dashboard/edit",
        params: { ws, dashboard: draft.name },
      });
    },
    onError: (err) => {
      setFailure(err instanceof Error ? err.message : String(err));
      // A refusal such as a stale proposal changes what the page says.
      void refresh();
    },
  });

  const discard = useMutation({
    mutationFn: () => dashboardsApi.discardProposal(ws, proposal.id),
    onSuccess: () => {
      setFailure(null);
      void refresh();
    },
    onError: (err) => {
      setFailure(err instanceof Error ? err.message : String(err));
      void refresh();
    },
  });

  function copyFile() {
    navigator.clipboard.writeText(proposal.result_source).then(
      () => toast.success("File copied"),
      () => toast.error("The file could not be copied."),
    );
  }

  const busy = accept.isPending || discard.isPending;
  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={`Proposed changes to ${proposal.title}`}
        description={
          <>
            The assistant proposed these changes to{" "}
            <Link
              to="/$ws/dashboards/$dashboard"
              params={{ ws, dashboard: proposal.dashboard }}
              className="text-[var(--brand-slate-blue)] underline"
            >
              {proposal.dashboard}
            </Link>
            . Nothing changes until you accept them.
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            {proposal.managed && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={copyFile}
              >
                <Copy className="size-3.5" aria-hidden />
                Copy file
              </Button>
            )}
            {(pending || proposal.status === "stale") && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                disabled={busy}
                onClick={() => discard.mutate()}
              >
                Discard
              </Button>
            )}
            {pending && !proposal.managed && (
              <Button
                size="sm"
                className="h-7 gap-1.5 text-xs"
                disabled={busy}
                onClick={() => {
                  setFailure(null);
                  accept.mutate();
                }}
              >
                {accept.isPending && (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                )}
                Accept as draft
              </Button>
            )}
          </div>
        }
      />
      <div className="space-y-2 px-4 pt-3">
        {proposal.managed && pending && (
          <Banner>
            This dashboard is managed in git, so these changes cannot be
            accepted here. Copy the file into the repository and apply it from
            there.
          </Banner>
        )}
        {note && <Banner>{note}</Banner>}
        {failure && (
          <p role="alert" className="text-xs text-destructive">
            {failure}
          </p>
        )}
        <section aria-label="Changes" className="space-y-1">
          {/* The model's words, shown as text: never as markdown or a link. */}
          <p className="text-sm text-text-primary">{proposal.summary}</p>
          <ul className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-text-secondary">
            {proposal.changes.map((change) => (
              <li key={change.path}>{describeChange(change)}</li>
            ))}
          </ul>
        </section>
      </div>
      <div className="min-h-0 flex-1">
        <EditorPreview
          ws={ws}
          preview={live.preview}
          current={live.current}
          refused={live.refused}
          text={proposal.result_source}
          onEdit={() => undefined}
          readOnly
        />
      </div>
    </div>
  );
}
