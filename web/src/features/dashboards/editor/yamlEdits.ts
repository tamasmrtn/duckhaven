import {
  isMap,
  isScalar,
  isSeq,
  parseDocument,
  YAMLMap,
  YAMLSeq,
  type Document,
} from "yaml";
import { GRID_COLUMNS } from "../layout";

// Changes the preview's controls make to the file. Each works on the YAML
// document rather than on the parsed value, so comments, key order and
// formatting the author chose survive, and each returns null when the file
// does not parse or holds no such widget or row.

/** The widths a widget can be set to, in columns of 12, and what each is called. */
export const WIDTH_PRESETS: [number, string][] = [
  [3, "A quarter"],
  [4, "A third"],
  [6, "Half"],
  [8, "Two thirds"],
  [9, "Three quarters"],
  [12, "The whole row"],
];

function read(text: string): Document | null {
  const doc = parseDocument(text, { uniqueKeys: true });
  return doc.errors.length ? null : doc;
}

function write(doc: Document): string {
  // No folding: a long title or SQL line stays on its line.
  return doc.toString({ lineWidth: 0 });
}

function rowsOf(doc: Document): YAMLSeq | null {
  const rows = doc.get("rows", true);
  return isSeq(rows) ? rows : null;
}

/** Where the widget with this id sits: its row's widget list and its index there. */
function locate(doc: Document, id: string) {
  for (const row of rowsOf(doc)?.items ?? []) {
    if (!isMap(row)) continue;
    const widgets = row.get("widgets", true);
    if (!isSeq(widgets)) continue;
    const index = widgets.items.findIndex(
      (w) => isMap(w) && w.get("id") === id,
    );
    if (index >= 0) return { widgets, index };
  }
  return null;
}

function swap(items: unknown[], index: number, by: -1 | 1): boolean {
  const other = index + by;
  if (other < 0 || other >= items.length) return false;
  [items[index], items[other]] = [items[other], items[index]];
  return true;
}

/** Move a widget one place left (-1) or right (1) in its row. */
export function moveWidget(text: string, id: string, by: -1 | 1) {
  const doc = read(text);
  const at = doc && locate(doc, id);
  if (!doc || !at || !swap(at.widgets.items, at.index, by)) return null;
  return write(doc);
}

/** Move a row one place up (-1) or down (1). */
export function moveRow(text: string, row: number, by: -1 | 1) {
  const doc = read(text);
  const rows = doc && rowsOf(doc);
  if (!doc || !rows || !swap(rows.items, row, by)) return null;
  return write(doc);
}

/** Give a widget a width, or with null let it share what its row leaves. */
export function setWidth(text: string, id: string, width: number | null) {
  const doc = read(text);
  const at = doc && locate(doc, id);
  const widget = at?.widgets.items[at.index];
  if (!doc || !isMap(widget)) return null;
  if (width === null) widget.delete("width");
  else widget.set("width", width);
  return write(doc);
}

/**
 * Whether a widget can take `width` beside the widths the rest of its row
 * sets. A row is at most 12 columns.
 */
export function fits(
  widths: (number | null | undefined)[],
  index: number,
  width: number,
): boolean {
  const others = widths.reduce<number>(
    (sum, w, i) => (i === index ? sum : sum + (w ?? 0)),
    0,
  );
  return others + width <= GRID_COLUMNS;
}

/** Every widget id the file uses. */
function widgetIds(doc: Document): Set<string> {
  const ids = new Set<string>();
  for (const row of rowsOf(doc)?.items ?? []) {
    const widgets = isMap(row) ? row.get("widgets", true) : null;
    if (!isSeq(widgets)) continue;
    for (const w of widgets.items) {
      const id = isMap(w) ? w.get("id") : null;
      if (typeof id === "string") ids.add(id);
    }
  }
  return ids;
}

/**
 * Add a widget in a row of its own at the end of the file. It keeps its id
 * unless a widget in the file has it already, when it takes the next number:
 * `trend` becomes `trend_2`. Fields without a value are left out, and SQL on
 * several lines is written as a block. Returns the new file and the widget's
 * id, or null when the file does not parse or is not a dashboard's map.
 */
export function addRow(
  text: string,
  widget: { id: string },
): { text: string; id: string } | null {
  const doc = read(text);
  if (!doc || !isMap(doc.contents)) return null;
  let rows = rowsOf(doc);
  // Rows that are something other than a list are left for the author.
  if (!rows && doc.get("rows") != null) return null;
  if (!rows) {
    rows = new YAMLSeq();
    doc.set("rows", rows);
  }
  rows.flow = false;
  const taken = widgetIds(doc);
  let id = widget.id;
  for (let n = 2; taken.has(id); n++) id = `${widget.id}_${n}`;
  const fields = Object.fromEntries(
    Object.entries({ ...widget, id }).filter(([, v]) => v != null),
  );
  const node = doc.createNode(fields) as YAMLMap;
  const sql = node.get("sql", true);
  if (
    isScalar(sql) &&
    typeof sql.value === "string" &&
    sql.value.includes("\n")
  ) {
    sql.type = "BLOCK_LITERAL";
  }
  const widgets = new YAMLSeq();
  widgets.add(node);
  const row = new YAMLMap();
  row.set("widgets", widgets);
  rows.add(row);
  return { text: write(doc), id };
}
