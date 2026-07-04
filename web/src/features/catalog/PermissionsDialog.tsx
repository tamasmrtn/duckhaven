import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PermissionsPanel } from "./PermissionsPanel";

/** The fully-qualified object path shown in the dialog title. */
function objectPath(catalog: string, schema?: string, table?: string): string {
  return [catalog, schema, table].filter(Boolean).join(".");
}

export function PermissionsDialog({
  ws,
  catalog,
  schema,
  table,
  open,
  onOpenChange,
}: {
  ws: string;
  catalog: string;
  schema?: string;
  table?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Permissions</DialogTitle>
          <DialogDescription>
            Grants on{" "}
            <span className="font-mono">
              {objectPath(catalog, schema, table)}
            </span>
            . A grant at a coarser level (catalog or schema) is inherited by
            everything beneath it.
          </DialogDescription>
        </DialogHeader>
        {open && (
          <PermissionsPanel
            ws={ws}
            catalog={catalog}
            schema={schema}
            table={table}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
