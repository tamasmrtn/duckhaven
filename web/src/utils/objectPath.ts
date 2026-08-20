interface RoutableObject {
  // Deliberately excludes "saved_query": unlike a schema/table, a saved query
  // has no plain object-detail route — opening one means stashing its SQL and
  // navigating to the worksheet (see openSavedQuery in CommandPalette.tsx),
  // not building a path from its name. Callers filter those out first.
  type: "schema" | "table";
  catalog?: string | null;
  schema_name?: string | null;
  name: string;
}

// A schema/table result carries enough to build its route on its own — no
// separate id, tables/schemas are name-addressed (see the search endpoint's
// docstring) — so navigation is just interpolating these parts into the
// existing catalog route. Shared by the command palette and the Catalog
// landing page's recently-viewed row, the two places that need to turn a
// recently-viewed/searched object into a destination. Each segment is
// percent-encoded since catalog/schema/table names are user-supplied and may
// contain characters that would otherwise produce a malformed path.
export function objectPath(ws: string, r: RoutableObject): string {
  const seg = (v: string | null | undefined) => encodeURIComponent(v ?? "");
  if (r.type === "schema")
    return `/${seg(ws)}/catalog/${seg(r.catalog)}/${seg(r.name)}`;
  return `/${seg(ws)}/catalog/${seg(r.catalog)}/${seg(r.schema_name)}/${seg(r.name)}`;
}
