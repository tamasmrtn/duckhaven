import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ALLOWED_COLUMN_TYPES,
  type AllowedColumnType,
  type ColumnSpec,
} from "@/api/schemas";
import { useCreateTable } from "@/queries/schemas.mutations";

type Row = {
  id: number;
  name: string;
  type: AllowedColumnType;
  nullable: boolean;
};

let _seq = 0;
const nextId = () => ++_seq;

function emptyRow(): Row {
  return { id: nextId(), name: "", type: "VARCHAR", nullable: true };
}

export function CreateTableDialog({
  ws,
  catalog,
  schema,
  open,
  onOpenChange,
}: {
  ws: string;
  catalog?: string;
  schema: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [rows, setRows] = useState<Row[]>([emptyRow()]);
  const [error, setError] = useState<string | null>(null);
  const create = useCreateTable(ws, catalog, schema);

  function reset() {
    setName("");
    setRows([emptyRow()]);
    setError(null);
  }

  function updateRow(id: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function removeRow(id: number) {
    setRows((rs) => (rs.length > 1 ? rs.filter((r) => r.id !== id) : rs));
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError("Table name is required");
      return;
    }
    const columns: ColumnSpec[] = [];
    for (const r of rows) {
      if (!r.name.trim()) {
        setError("Every column needs a name");
        return;
      }
      columns.push({ name: r.name.trim(), type: r.type, nullable: r.nullable });
    }
    try {
      await create.mutateAsync({ name: name.trim(), columns });
      reset();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create table");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New table in {schema}</DialogTitle>
          <DialogDescription>
            Define the table name and its columns; it is created in the {schema}{" "}
            schema.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="table-name">Name</Label>
            <Input
              id="table-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="events"
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <Label>Columns</Label>
            <div className="space-y-1.5">
              {rows.map((r) => (
                <div key={r.id} className="flex items-center gap-2">
                  <Input
                    aria-label="column name"
                    value={r.name}
                    onChange={(e) => updateRow(r.id, { name: e.target.value })}
                    placeholder="ts"
                    className="flex-1"
                  />
                  <Select
                    value={r.type}
                    onValueChange={(v) =>
                      updateRow(r.id, { type: v as AllowedColumnType })
                    }
                  >
                    <SelectTrigger aria-label="column type" className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ALLOWED_COLUMN_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {t}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <label className="flex items-center gap-1 text-xs text-text-secondary">
                    <input
                      type="checkbox"
                      aria-label="nullable"
                      checked={r.nullable}
                      onChange={(e) =>
                        updateRow(r.id, { nullable: e.target.checked })
                      }
                    />
                    null?
                  </label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="remove column"
                    onClick={() => removeRow(r.id)}
                    disabled={rows.length === 1}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRows((rs) => [...rs, emptyRow()])}
              className="gap-1.5"
            >
              <Plus className="size-3.5" />
              Add column
            </Button>
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
