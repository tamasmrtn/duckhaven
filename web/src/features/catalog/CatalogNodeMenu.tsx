import { type ReactNode, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Plus,
  Table2,
  Pencil,
  Trash2,
  ExternalLink,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  useDeleteTable,
  useDropSchema,
  useRecountTable,
} from "@/queries/schemas.mutations";
import { CreateSchemaDialog } from "./CreateSchemaDialog";
import { CreateTableDialog } from "./CreateTableDialog";
import { ConfirmDropDialog } from "./ConfirmDropDialog";
import {
  alterTemplate,
  selectTemplate,
  stashWorksheetSql,
} from "./worksheetSql";

export type CatalogNode =
  | { kind: "catalog" }
  | { kind: "schema"; schema: string }
  | { kind: "table"; schema: string; table: string };

// Right-click DDL menu for a catalog node. Renders the appropriate actions for
// the node level and owns the create/drop dialogs. The same actions back both
// the catalog page and the worksheet sidebar tree.
export function CatalogNodeMenu({
  ws,
  node,
  children,
  onDropped,
}: {
  ws: string;
  node: CatalogNode;
  children: ReactNode;
  onDropped?: () => void;
}) {
  const navigate = useNavigate();
  const [createSchemaOpen, setCreateSchemaOpen] = useState(false);
  const [createTableOpen, setCreateTableOpen] = useState(false);
  const [dropOpen, setDropOpen] = useState(false);

  const dropSchema = useDropSchema(ws);
  const deleteTable = useDeleteTable(
    ws,
    node.kind === "table" ? node.schema : "",
  );
  const recountTable = useRecountTable(
    ws,
    node.kind === "table" ? node.schema : "",
  );

  function openInWorksheet(sql: string) {
    stashWorksheetSql(ws, sql);
    navigate({ to: "/$ws/worksheets", params: { ws } });
  }

  async function recount(table: string) {
    try {
      const { row_count } = await recountTable.mutateAsync(table);
      toast.success(
        row_count == null
          ? `Recounted ${table}`
          : `${table}: ${row_count.toLocaleString()} rows`,
      );
    } catch {
      toast.error(`Couldn't recount ${table} — no agent connected.`);
    }
  }

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
        <ContextMenuContent>
          {node.kind === "catalog" && (
            <ContextMenuItem onSelect={() => setCreateSchemaOpen(true)}>
              <Plus />
              Create schema
            </ContextMenuItem>
          )}
          {node.kind === "schema" && (
            <>
              <ContextMenuItem onSelect={() => setCreateTableOpen(true)}>
                <Table2 />
                Create table
              </ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuItem destructive onSelect={() => setDropOpen(true)}>
                <Trash2 />
                Drop schema
              </ContextMenuItem>
            </>
          )}
          {node.kind === "table" && (
            <>
              <ContextMenuItem
                onSelect={() =>
                  openInWorksheet(selectTemplate(node.schema, node.table))
                }
              >
                <ExternalLink />
                Query in worksheet
              </ContextMenuItem>
              <ContextMenuItem
                onSelect={() =>
                  openInWorksheet(alterTemplate(node.schema, node.table))
                }
              >
                <Pencil />
                Alter table
              </ContextMenuItem>
              <ContextMenuItem onSelect={() => recount(node.table)}>
                <RefreshCw />
                Recount rows
              </ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuItem destructive onSelect={() => setDropOpen(true)}>
                <Trash2 />
                Drop table
              </ContextMenuItem>
            </>
          )}
        </ContextMenuContent>
      </ContextMenu>

      {node.kind === "catalog" && (
        <CreateSchemaDialog
          ws={ws}
          open={createSchemaOpen}
          onOpenChange={setCreateSchemaOpen}
        />
      )}
      {node.kind === "schema" && (
        <>
          <CreateTableDialog
            ws={ws}
            schema={node.schema}
            open={createTableOpen}
            onOpenChange={setCreateTableOpen}
          />
          <ConfirmDropDialog
            open={dropOpen}
            onOpenChange={setDropOpen}
            kind="schema"
            name={node.schema}
            pending={dropSchema.isPending}
            onConfirm={async (cascade) => {
              await dropSchema.mutateAsync({ schema: node.schema, cascade });
              toast.success(`Dropped schema ${node.schema}`);
              onDropped?.();
            }}
          />
        </>
      )}
      {node.kind === "table" && (
        <ConfirmDropDialog
          open={dropOpen}
          onOpenChange={setDropOpen}
          kind="table"
          name={node.table}
          pending={deleteTable.isPending}
          onConfirm={async () => {
            await deleteTable.mutateAsync(node.table);
            toast.success(`Dropped ${node.schema}.${node.table}`);
            onDropped?.();
          }}
        />
      )}
    </>
  );
}
