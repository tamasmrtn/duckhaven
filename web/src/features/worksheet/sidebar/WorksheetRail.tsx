import { useState } from "react";
import { Segmented } from "@/components/ui/segmented";
import { CatalogTree } from "@/features/catalog/CatalogTree";
import type { SavedQuery } from "@/types/saved-query";
import { WorksheetBrowser } from "./WorksheetBrowser";

export type RailView = "worksheets" | "catalog";

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

/** Which view the sidebar shows, remembered in this browser. */
export function useRailView(): [RailView, (next: RailView) => void] {
  const [view, setView] = useState<RailView>(loadView);
  function change(next: RailView) {
    setView(next);
    try {
      localStorage.setItem(railKey, next);
    } catch {
      // Only the remembered choice is lost.
    }
  }
  return [view, change];
}

/** The Worksheets | Catalog switch. The page places it in the tab row. */
export function RailSwitch({
  view,
  onChange,
}: {
  view: RailView;
  onChange: (next: RailView) => void;
}) {
  return (
    <Segmented
      label="Sidebar"
      hideLabel
      value={view}
      onChange={onChange}
      options={[
        { value: "worksheets", label: "Worksheets" },
        { value: "catalog", label: "Catalog" },
      ]}
    />
  );
}

interface WorksheetRailProps {
  ws: string;
  workspaceName: string | undefined;
  view: RailView;
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
 * saved queries, as in Snowsight's editor sidebar and the Databricks editor's
 * workspace/catalog panels. It has no header of its own: the switch between the
 * two lives in the tab row above it, so the pane and the editor share one top
 * row and one divider, as in Databricks.
 */
export function WorksheetRail({
  ws,
  workspaceName,
  view,
  onTableClick,
  ...browser
}: WorksheetRailProps) {
  return (
    <div className="h-full overflow-hidden">
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
  );
}
