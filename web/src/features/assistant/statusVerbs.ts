// Whimsical "still working" status words shown between Send and the first
// token/tool call, and while a tool call is in flight. Duck idioms doubling as
// data-domain terms (dabbling, upending, migrating) so the wait reads as
// DuckHaven's voice rather than generic chatbot flavor text — deliberately
// never interpolates the specific table/column being touched, so a status
// like "Profiling `customers`…" can't read as profiling a named data subject.
// Shared by the three "listing" tools so the pools can't drift out of sync.
const LIST_VERBS = [
  "Waddling through the catalog…",
  "Scouting the shoreline…",
  "Herding the ducklings…",
];

const VERB_POOLS: Record<string, string[]> = {
  run_sql: [
    "Quacking the numbers…",
    "Diving for rows…",
    "Paddling upstream…",
    "Quacking through joins…",
  ],
  list_catalogs: LIST_VERBS,
  list_schemas: LIST_VERBS,
  list_tables: LIST_VERBS,
  describe_table: ["Preening the schema…", "Dabbling in the data…"],
  get_query_result: ["Surfacing results…", "Bobbing along…"],
  get_worksheet_sql: ["Dabbling in the data…"],
  get_worksheet_selection: ["Dabbling in the data…"],
  propose_sql_edit: ["Drafting a nest…", "Nesting the answer…"],
  search_semantic: ["Consulting the flock…", "Looking up what that means…"],
  get_semantic_model: ["Reading the definitions…", "Consulting the flock…"],
  query_metric: [
    "Measuring by the book…",
    "Quacking the agreed numbers…",
    "Diving for rows…",
  ],
  explain_metric: ["Reading the definitions…", "Checking what counts…"],
};

// Shown before the first tool call arrives, or for a tool with no dedicated pool.
const FALLBACK_POOL = [
  "Migrating data…",
  "Upending for insights…",
  "Quacking the numbers…",
  "Waddling through the catalog…",
  "Preening the schema…",
  "Dabbling in the data…",
  "Surfacing results…",
  "Drafting a nest…",
];

function poolFor(tool: string | null): string[] {
  if (tool && VERB_POOLS[tool]) return VERB_POOLS[tool];
  return FALLBACK_POOL;
}

/** The status word for a tool (or the fallback pool before one starts), picked
 * deterministically from a rotation tick so it's testable without real timers. */
export function pickVerb(tool: string | null, tick: number): string {
  const pool = poolFor(tool);
  return pool[tick % pool.length];
}
