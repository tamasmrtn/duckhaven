import { Link, useParams } from "@tanstack/react-router";
import { Eye, LayoutDashboard } from "lucide-react";
import { ApiError } from "@/api/client";
import { Banner } from "@/components/ui/banner";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboardPreview } from "@/queries/dashboards";
import { EditorPreview } from "./EditorPreview";

const ENDED =
  "A preview lasts two hours after its file last changed or it last ran, and only the person who made it can open it. Preview the file again to see it here.";

/**
 * A dashboard file held as a preview, from `dh dashboard preview`, run as the
 * person viewing it. The page follows the file: each save the command sends
 * runs again here. Nothing on it is stored as a dashboard.
 */
export function DashboardPreviewPage() {
  const { ws, preview: id } = useParams({
    from: "/$ws/dashboard-previews/$preview",
  });
  const { data, isLoading, error } = useDashboardPreview(ws, id);

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  const missing = error instanceof ApiError && error.status === 404;
  if (!data) {
    return (
      <EmptyState
        icon={LayoutDashboard}
        title={
          missing ? "This preview has ended" : "This preview could not be read"
        }
        description={
          missing ? ENDED : error instanceof Error ? error.message : undefined
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
  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={data.spec.title}
        description={`A preview of ${data.spec.name}.dashboard.yml. It runs again each time the file is saved, and nothing here is stored as a dashboard.`}
        badge={
          <span className="inline-flex items-center gap-1 rounded border border-[var(--border-strong)] px-1.5 py-0.5 text-2xs font-medium text-text-secondary">
            <Eye className="size-3" aria-hidden />
            preview
          </span>
        }
      />
      {missing && (
        <Banner className="mx-4 mt-3">
          <span>
            This preview has ended, so the page shows the file as it last was.{" "}
            {ENDED}
          </span>
        </Banner>
      )}
      <div className="min-h-0 flex-1">
        <EditorPreview
          ws={ws}
          preview={data}
          current
          refused={false}
          text={data.source_text ?? ""}
          onEdit={() => undefined}
          readOnly
        />
      </div>
    </div>
  );
}
