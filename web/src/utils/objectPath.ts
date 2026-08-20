interface RoutableObject {
  type: "schema" | "table" | "saved_query";
  catalog?: string | null;
  schema_name?: string | null;
  name: string;
}

// A schema/table result carries enough to build its route on its own — no
// separate id, tables/schemas are name-addressed (see the search endpoint's
// docstring) — so navigation is just interpolating these parts into the
// existing catalog route. Shared by the command palette and the Catalog
// landing page's recently-viewed row, the two places that need to turn a
// recently-viewed/searched object into a destination.
export function objectPath(ws: string, r: RoutableObject): string {
  if (r.type === "schema") return `/${ws}/catalog/${r.catalog}/${r.name}`;
  return `/${ws}/catalog/${r.catalog}/${r.schema_name}/${r.name}`;
}
