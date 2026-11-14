import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useBlocker,
  useNavigate,
  useParams,
} from "@tanstack/react-router";
import {
  AlertCircle,
  AlertTriangle,
  GitBranch,
  History,
  LayoutDashboard,
  Loader2,
  Save,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { stringify } from "yaml";
import { ApiError } from "@/api/client";
import { dashboardsApi } from "@/api/dashboards";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard, useDashboardSchema } from "@/queries/dashboards";
import type { Dashboard, Diagnostic } from "@/types/dashboard";
import { relativeTime } from "@/utils/relativeTime";
import { DashboardBadges } from "../DashboardBadges";
import { useIsOwner } from "../useIsOwner";
import {
  ConflictDialog,
  DiscardDialog,
  LeaveDialog,
  PublishDialog,
  savedWhen,
} from "./dialogs";
import { EditorPreview } from "./EditorPreview";
import { HistorySheet } from "./HistorySheet";
import { useLiveFile } from "./useLiveFile";
import { YamlEditor, type YamlEditorHandle } from "./YamlEditor";

/** The file as YAML: as it was written, or written out from what was sent as JSON. */
function fileOf(d: { source_text: string | null; spec: object }): string {
  return d.source_text ?? stringify(d.spec, { lineWidth: 0 });
}

function Problems({
  errors,
  warnings,
  checking,
  onReveal,
}: {
  errors: Diagnostic[];
  warnings: Diagnostic[];
  checking: boolean;
  onReveal: (d: Diagnostic) => void;
}) {
  const all = [
    ...errors.map((d) => ({ d, error: true })),
    ...warnings.map((d) => ({ d, error: false })),
  ];
  return (
    <section
      aria-label="Problems"
      className="max-h-44 shrink-0 overflow-auto border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2"
    >
      <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-text-secondary">
        {checking && <Loader2 className="size-3 animate-spin" aria-hidden />}
        {all.length === 0
          ? checking
            ? "Checking the file"
            : "No problems: it can be published as it stands."
          : `${errors.length} ${errors.length === 1 ? "error" : "errors"}, ${warnings.length} ${warnings.length === 1 ? "warning" : "warnings"}`}
      </p>
      <ul className="space-y-0.5">
        {all.map(({ d, error }, i) => (
          <li key={i}>
            <button
              type="button"
              onClick={() => onReveal(d)}
              className="flex w-full items-start gap-1.5 rounded px-1 py-0.5 text-left text-xs hover:bg-[var(--bg-hover)]"
            >
              {error ? (
                <AlertCircle
                  className="mt-0.5 size-3 shrink-0 text-[var(--status-failed)]"
                  aria-label="Error"
                />
              ) : (
                <AlertTriangle
                  className="mt-0.5 size-3 shrink-0 text-[var(--status-running)]"
                  aria-label="Warning"
                />
              )}
              <span className="min-w-0">
                <span className="font-mono text-2xs text-text-tertiary">
                  {d.line != null ? `Line ${d.line}` : d.path || "File"}
                </span>{" "}
                <span className="text-text-primary">{d.message}</span>
                {d.hint && (
                  <span className="block text-2xs text-text-tertiary">
                    {d.hint}
                  </span>
                )}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Editing({ ws, initial }: { ws: string; initial: Dashboard }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isOwner = useIsOwner(ws);
  const { data: schema } = useDashboardSchema();
  const [dashboard, setDashboard] = useState(initial);
  const [saved, setSaved] = useState(() => fileOf(initial));
  const [text, setText] = useState(saved);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState<Dashboard | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const editor = useRef<YamlEditorHandle>(null);
  const live = useLiveFile(ws, text);
  const dirty = text !== saved;
  const managed = dashboard.managed;
  const published = dashboard.status === "published";

  const blocker = useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: () => dirty,
    withResolver: true,
  });

  /** Take what the server answered as the dashboard this editor works on. */
  function accept(next: Dashboard, file: string) {
    setDashboard(next);
    setSaved(file);
    queryClient.setQueryData(
      ["workspace", ws, "dashboard", next.name, "draft"],
      next,
    );
    void queryClient.invalidateQueries({
      queryKey: ["workspace", ws, "dashboards"],
    });
    void queryClient.invalidateQueries({
      queryKey: ["workspace", ws, "dashboard", dashboard.name],
      exact: true,
    });
    if (next.name !== dashboard.name) {
      // Renamed, at once if it was never published: follow it.
      void navigate({
        to: "/$ws/dashboards/$dashboard/edit",
        params: { ws, dashboard: next.name },
        replace: true,
        ignoreBlocker: true,
      });
    }
  }

  function refused(err: unknown, doing: string) {
    if (err instanceof ApiError && err.code === "dashboard_conflict") {
      const current = err.details?.current as Dashboard | undefined;
      if (current) {
        setPublishOpen(false);
        setConflict(current);
        return;
      }
    }
    if (err instanceof ApiError && err.code === "invalid_dashboard") {
      // What is wrong is marked in the file, so get out of its way.
      setPublishOpen(false);
      toast.error(err.message, {
        description: "The problems are listed under the editor.",
      });
      return;
    }
    toast.error(
      err instanceof Error
        ? err.message
        : `The dashboard could not be ${doing}.`,
    );
  }

  async function save(base = dashboard.version): Promise<Dashboard | null> {
    if (managed) return null;
    const file = text;
    setSaving(true);
    try {
      const next = await dashboardsApi.update(ws, dashboard.name, file, base);
      accept(next, file);
      toast.success("Draft saved");
      return next;
    } catch (err) {
      refused(err, "saved");
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function publish(message: string | null) {
    let current = dashboard;
    if (dirty) {
      const next = await save();
      if (!next) return;
      current = next;
    }
    try {
      const next = await dashboardsApi.publish(
        ws,
        current.name,
        current.version,
        message,
      );
      accept(next, text);
      setPublishOpen(false);
      void queryClient.invalidateQueries({
        queryKey: ["workspace", ws, "dashboard", next.name, "revisions"],
      });
      toast.success("Published", {
        description: "Everyone who can open the dashboard sees this version.",
      });
    } catch (err) {
      refused(err, "published");
    }
  }

  async function discard() {
    try {
      await dashboardsApi.discardDraft(ws, dashboard.name);
      const next = await dashboardsApi.get(ws, dashboard.name, true);
      const file = fileOf(next);
      accept(next, file);
      setText(file);
      setDiscardOpen(false);
      toast.success("Draft discarded");
    } catch (err) {
      refused(err, "changed");
    }
  }

  function loadTheirs(current: Dashboard) {
    const file = fileOf(current);
    setConflict(null);
    accept(current, file);
    setText(file);
  }

  function keepMine(current: Dashboard) {
    setConflict(null);
    void save(current.version);
  }

  async function openRevision(number: number) {
    try {
      const revision = await dashboardsApi.revision(ws, dashboard.name, number);
      setText(fileOf(revision));
      setHistoryOpen(false);
      toast.success(`Revision ${number} is in the editor`, {
        description: "Save it as the draft and publish it to restore it.",
      });
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "That revision could not be read.",
      );
    }
  }

  const status = dirty
    ? "Unsaved changes"
    : !published || dashboard.has_draft
      ? `Draft saved ${savedWhen(dashboard)}`
      : "No changes since it was published";

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={dashboard.title}
        description="Edit the dashboard's file. The preview runs each change as you."
        badge={<DashboardBadges dashboard={dashboard} />}
        actions={
          <div className="flex flex-wrap items-center gap-2 text-2xs text-text-tertiary">
            <span aria-live="polite">{saving ? "Saving…" : status}</span>
            {published && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={() => setHistoryOpen(true)}
              >
                <History className="size-3.5" aria-hidden />
                Revisions
              </Button>
            )}
            {!managed && published && dashboard.has_draft && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={() => setDiscardOpen(true)}
              >
                <Undo2 className="size-3.5" aria-hidden />
                Discard draft
              </Button>
            )}
            {!managed && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={() => void save()}
                disabled={!dirty || saving}
                title="Ctrl+S or ⌘S"
              >
                <Save className="size-3.5" aria-hidden />
                Save draft
              </Button>
            )}
            {!managed && isOwner && (
              <Button
                variant="brand"
                size="sm"
                className="h-7 text-xs"
                onClick={() => setPublishOpen(true)}
                disabled={
                  saving || (!dirty && published && !dashboard.has_draft)
                }
              >
                Publish
              </Button>
            )}
            <Button asChild variant="outline" size="sm" className="h-7 text-xs">
              <Link
                to="/$ws/dashboards/$dashboard"
                params={{ ws, dashboard: dashboard.name }}
              >
                Done
              </Link>
            </Button>
          </div>
        }
      />
      <div className="space-y-2 px-4 pt-3 empty:hidden md:px-6">
        {managed ? (
          <Banner>
            <GitBranch className="size-3.5 shrink-0" aria-hidden />
            <span>
              Managed in git
              {dashboard.source_id && (
                <>
                  {" "}
                  as <span className="font-mono">{dashboard.source_id}</span>
                </>
              )}
              , so the editor only reads it. Change it by changing its file
              there and applying it.
            </span>
          </Banner>
        ) : !published ? (
          <Banner>
            <span>
              <strong className="font-semibold">Draft.</strong> Nobody has
              published this dashboard, so only writers can see it.
              {!isOwner && " A workspace owner publishes it."}
            </span>
          </Banner>
        ) : (
          (dashboard.has_draft || dirty) && (
            <Banner>
              <span>
                <strong className="font-semibold">Editing the draft.</strong>{" "}
                Readers see the version published{" "}
                {relativeTime(dashboard.published_at)} until
                {isOwner ? " you publish" : " a workspace owner publishes"} the
                draft.
              </span>
            </Banner>
          )
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto pt-3 lg:grid-cols-2 lg:overflow-hidden">
        <div className="flex min-h-[28rem] flex-col border-[var(--border-subtle)] lg:min-h-0 lg:border-r">
          <div className="min-h-0 flex-1">
            <YamlEditor
              ref={editor}
              path={`${dashboard.name}.dashboard.yml`}
              value={text}
              onChange={setText}
              onSave={() => dirty && !saving && void save()}
              readOnly={managed}
              schema={schema}
              errors={live.errors}
              warnings={live.warnings}
            />
          </div>
          <Problems
            errors={live.errors}
            warnings={live.warnings}
            checking={live.checking}
            onReveal={(d) => editor.current?.reveal(d.line ?? 1, d.col ?? 1)}
          />
        </div>
        <div className="min-h-[28rem] lg:min-h-0">
          {live.failure && (
            <p
              role="alert"
              className="px-4 py-2 text-xs text-[var(--status-failed)]"
            >
              {live.failure}
            </p>
          )}
          <EditorPreview
            ws={ws}
            preview={live.preview}
            current={live.current}
            refused={live.refused}
            text={text}
            onEdit={setText}
            readOnly={managed}
          />
        </div>
      </div>
      <PublishDialog
        open={publishOpen}
        onOpenChange={setPublishOpen}
        dirty={dirty}
        onPublish={publish}
      />
      <DiscardDialog
        open={discardOpen}
        onOpenChange={setDiscardOpen}
        onDiscard={discard}
      />
      <ConflictDialog
        current={conflict}
        base={dashboard.version}
        onCancel={() => setConflict(null)}
        onLoadTheirs={loadTheirs}
        onKeepMine={keepMine}
      />
      <HistorySheet
        ws={ws}
        name={dashboard.name}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onOpen={(n) => void openRevision(n)}
      />
      <LeaveDialog
        open={blocker.status === "blocked"}
        onStay={() => blocker.reset?.()}
        onLeave={() => blocker.proceed?.()}
      />
    </div>
  );
}

/** A dashboard's file in an editor, beside a preview that runs it as you. */
export function DashboardEditorPage() {
  const { ws, dashboard: name } = useParams({
    from: "/$ws/dashboards/$dashboard/edit",
  });
  const { data, isLoading, error } = useDashboard(ws, name, true);

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!data) {
    const status = error instanceof ApiError ? error.status : undefined;
    return (
      <EmptyState
        icon={LayoutDashboard}
        title={
          status === 403
            ? "Editing a dashboard needs the writer role"
            : status === 404
              ? `No dashboard called ${name}`
              : "This dashboard could not be read"
        }
        description={
          status === 403
            ? "Ask a workspace owner to make you a writer."
            : status === 404
              ? "It may have been deleted or renamed."
              : error instanceof Error
                ? error.message
                : undefined
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
  // Keyed by id, so a rename keeps the editor and what it holds.
  return <Editing key={data.id} ws={ws} initial={data} />;
}
