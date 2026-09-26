import type { ReactNode } from "react";
import {
  Check,
  ChevronDown,
  CloudOff,
  Loader2,
  Play,
  Ruler,
  Save,
  Settings2,
  Square,
  TriangleAlert,
} from "lucide-react";
import { AgentPicker } from "@/components/app/AgentPicker";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { Catalog } from "@/types/catalog";
import type { BackendKind } from "@/types/storage-backend";
import { CatalogSelect } from "./CatalogSelect";
import type { SyncStatus } from "./state/worksheetSync";

function SaveStatusText({
  status,
  edited,
}: {
  status: SyncStatus | undefined;
  edited: boolean;
}) {
  // A worksheet nobody has typed in yet has nothing to report, so "Saved"
  // waits for the first edit, as in Snowsight and Databricks.
  const quiet = !edited && (status === undefined || status === "idle");
  const content = quiet ? null : status === "saving" ? (
    <>
      <Loader2 className="size-3 animate-spin" /> Saving…
    </>
  ) : status === "error" ? (
    <>
      <CloudOff className="size-3 text-[var(--status-running)]" /> Offline —
      retrying
    </>
  ) : status === "conflict" ? (
    <>
      <TriangleAlert className="size-3 text-[var(--status-running)]" /> Not
      saved
    </>
  ) : (
    <>
      <Check className="size-3" /> Saved
    </>
  );
  return (
    <span
      aria-live="polite"
      title={quiet ? undefined : "Worksheets save automatically"}
      className="flex items-center gap-1 text-2xs text-text-tertiary"
    >
      {content}
    </span>
  );
}

interface WorksheetToolbarProps {
  ws: string;
  leading?: ReactNode;
  // Agent
  agentId: string | null;
  onAgentChange: (id: string) => void;
  pickerOpen: boolean;
  onPickerOpenChange: (open: boolean) => void;
  workspaceBackend?: BackendKind;
  catalogs: Catalog[];
  catalog: string | undefined;
  onCatalogChange: (slug: string) => void;
  timeoutMinutes: number;
  onTimeoutChange: (minutes: number) => void;
  saveStatus: SyncStatus | undefined;
  // The worksheet's content has been saved at least once since it was made.
  edited: boolean;
  // Run
  isRunning: boolean;
  runDisabled: boolean;
  // The agent is stopped; running starts it first.
  willStart: boolean;
  onRun: () => void;
  onCancel: () => void;
  // Save
  linkedName: string | null;
  diverged: boolean;
  onSave: () => void;
  onSaveAs: () => void;
  onRevert: () => void;
  onSaveAsMetric: () => void;
}

export function WorksheetToolbar(props: WorksheetToolbarProps) {
  const {
    ws,
    leading,
    agentId,
    onAgentChange,
    pickerOpen,
    onPickerOpenChange,
    workspaceBackend,
    catalogs,
    catalog,
    onCatalogChange,
    timeoutMinutes,
    onTimeoutChange,
    saveStatus,
    edited,
    isRunning,
    runDisabled,
    willStart,
    onRun,
    onCancel,
    linkedName,
    diverged,
    onSave,
    onSaveAs,
    onRevert,
    onSaveAsMetric,
  } = props;
  return (
    // py-2 matches the sidebar's padding, so the agent picker and the sidebar's
    // search box (both 32px) line up exactly.
    <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 shrink-0">
      {leading}
      <AgentPicker
        ws={ws}
        value={agentId}
        onChange={onAgentChange}
        open={pickerOpen}
        onOpenChange={onPickerOpenChange}
        workspaceBackend={workspaceBackend}
        workspaceCatalogKinds={catalogs.map((c) => c.kind)}
        // The API starts a stopped elastic agent for a worksheet run.
        allowTerminatedElastic
      />
      {catalogs.length > 0 && (
        <CatalogSelect
          catalogs={catalogs}
          value={catalog}
          onChange={onCatalogChange}
        />
      )}
      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label="Query settings"
          >
            <Settings2 className="size-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-56 space-y-3 p-3" align="start">
          <div className="space-y-1">
            <Label htmlFor="worksheet-timeout" className="text-xs">
              Timeout (min)
            </Label>
            <Input
              id="worksheet-timeout"
              type="number"
              min={1}
              max={120}
              value={timeoutMinutes}
              onChange={(e) => {
                const minutes = Number(e.target.value);
                if (minutes >= 1 && minutes <= 120) onTimeoutChange(minutes);
              }}
              className="h-7 text-xs"
            />
            <p className="text-2xs text-text-tertiary">
              Remembered for this worksheet.
            </p>
          </div>
        </PopoverContent>
      </Popover>

      <div className="ml-auto flex items-center gap-1">
        <SaveStatusText status={saveStatus} edited={edited} />
        {isRunning ? (
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={onCancel}
          >
            <Square className="size-3" />
            Cancel
          </Button>
        ) : (
          <Button
            size="sm"
            className="h-8 gap-1.5 bg-[var(--brand-yellow)] text-black hover:bg-yellow-300 text-xs font-medium animate-run-pulse-trigger"
            onClick={onRun}
            disabled={runDisabled}
            aria-label={
              willStart ? "Start agent and run query (⌘↵)" : "Run query (⌘↵)"
            }
          >
            <Play className="size-3 fill-black" />
            {willStart ? "Start & run" : "Run"}
            <kbd className="ml-0.5 font-mono text-2xs opacity-60">⌘↵</kbd>
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-8 text-xs"
          title="Save the selected expression as a metric"
          onClick={onSaveAsMetric}
        >
          <Ruler className="size-3.5 mr-1" />
          Save as metric…
        </Button>
        {linkedName ? (
          <div className="flex items-center">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 rounded-r-none text-xs"
              onClick={onSave}
              title={`Save to “${linkedName}” (⌘S)`}
            >
              <Save className="size-3.5 mr-1" />
              Save
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 rounded-l-none px-1.5"
                  aria-label="More save options"
                >
                  <ChevronDown className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={onSaveAs}>
                  Save as…
                </DropdownMenuItem>
                <DropdownMenuItem disabled={!diverged} onSelect={onRevert}>
                  Revert to saved
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 text-xs"
            onClick={onSaveAs}
          >
            <Save className="size-3.5 mr-1" />
            Save…
          </Button>
        )}
      </div>
    </div>
  );
}
