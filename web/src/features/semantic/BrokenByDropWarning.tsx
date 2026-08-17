import { useTableSemantics } from "@/queries/semantic";
import { plural } from "@/utils";

/**
 * Which published business definitions a drop is about to break.
 *
 * Shown inside the drop confirmation because this is the one consequence the
 * catalog cannot work out for itself: dropping a table is obviously destructive
 * to the table, and not at all obviously destructive to the definition of
 * revenue that four teams quote in meetings.
 *
 * Renders nothing when nothing depends on it, so the dialog stays quiet in the
 * common case.
 */
export function BrokenByDropWarning({
  ws,
  catalog,
  schema,
  table,
}: {
  ws: string;
  catalog: string;
  schema: string;
  table: string;
}) {
  const { data } = useTableSemantics(ws, catalog, schema, table);
  const published = (data?.dependents ?? []).filter(
    (d) => d.model_status === "published",
  );

  if (published.length === 0) return null;

  return (
    <div className="rounded border border-[var(--status-failed)] bg-[var(--status-failed)]/10 p-2 text-xs">
      <p className="font-medium">
        This will break {plural(published.length, "published definition")}.
      </p>
      <ul className="mt-1 space-y-0.5 text-text-secondary">
        {published.map((d) => (
          <li key={`${d.model}-${d.name}`}>
            <span className="font-mono">{d.name}</span> in {d.model_name}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-text-tertiary">
        They stay defined and become repairable by rebinding — they are not
        deleted.
      </p>
    </div>
  );
}
