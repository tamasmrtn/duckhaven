import { useState } from "react";
import { Segmented } from "@/components/ui/segmented";
import { CatalogTree } from "@/features/catalog/CatalogTree";
import type { SavedQuery } from "@/types/saved-query";
import { WorksheetBrowser } from "./WorksheetBrowser";

type RailView = "worksheets" | "catalog";

const railKey = "dh-worksheet-rail";

function loadView(): RailView {
  try {
    return localStorage.getItem(railKey) === "worksheets"
      ? "worksheets"
      : "catalog";
  } catch {
    return "catalog";
  }
}

interface WorksheetRailProps {
  ws: string;
  workspaceName: string | undefined;
  openIds: Set<string>;
  activeId: string | undefined;
  onOpen: (id: string) => void;
  onOpenSaved: (query: SavedQuery) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => Promise<void>;
  onTableClick?: () => void;
}

/**
 * The worksheet's left pane: the catalog tree, or the list of worksheets and
 * saved queries. One switch, as in Snowsight's editor sidebar and the
 * Databricks editor's workspace/catalog panels.
 */
export function WorksheetRail({
  ws,
  workspaceName,
  onTableClick,
  ...browser
}: WorksheetRailProps) {
  const [view, setView] = useState<RailView>(loadView);

  function change(next: RailView) {
    setView(next);
    try {
      localStorage.setItem(railKey, next);
    } catch {
      // Only the remembered choice is lost.
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] px-2 py-1.5">
        <Segmented
          label="Sidebar"
          hideLabel
          value={view}
          onChange={change}
          options={[
            { value: "worksheets", label: "Worksheets" },
            { value: "catalog", label: "Catalog" },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        {view === "worksheets" ? (
          <WorksheetBrowser ws={ws} {...browser} />
        ) : (
          <CatalogTree
            ws={ws}
            workspaceName={workspaceName}
            onTableClick={() => onTableClick?.()}
            onMetaViewClick={() => {}}
          />
        )}
      </div>
    </div>
  );
}
