import { activeStatementBounds } from "../statements";
import type { Clause, CursorContext, TableRef } from "./types";

const IDENT = /[A-Za-z0-9_$]/;

// Words that cannot be a table alias (they begin the next clause/join).
const ALIAS_STOPWORDS = new Set([
  "on",
  "using",
  "where",
  "group",
  "order",
  "having",
  "limit",
  "offset",
  "join",
  "inner",
  "left",
  "right",
  "full",
  "cross",
  "natural",
  "union",
  "intersect",
  "except",
  "set",
  "qualify",
  "returning",
  "as",
  "tablesample",
]);

function unquote(s: string): string {
  return s.startsWith('"') && s.endsWith('"') ? s.slice(1, -1) : s;
}

// The identifier ending at `pos` (exclusive) and where it starts.
function wordBefore(
  text: string,
  pos: number,
): { word: string; start: number } {
  let i = pos;
  while (i > 0 && IDENT.test(text[i - 1])) i--;
  return { word: text.slice(i, pos), start: i };
}

// Collect a dotted qualifier (`a.`, `schema.table.`) immediately before `start`.
function parseQualifier(text: string, start: number): string[] {
  const parts: string[] = [];
  let i = start;
  while (i > 0 && text[i - 1] === ".") {
    const prev = wordBefore(text, i - 1);
    if (!prev.word) break;
    parts.unshift(unquote(prev.word));
    i = prev.start;
  }
  return parts;
}

// Tables referenced in the statement's FROM/JOIN/INTO/UPDATE, best-effort.
export function referencedTables(statement: string): TableRef[] {
  const re =
    /\b(?:from|join|into|update)\s+("?[A-Za-z_$][\w$]*"?)(?:\s*\.\s*("?[A-Za-z_$][\w$]*"?))?(?:\s+(?:as\s+)?("?[A-Za-z_$][\w$]*"?))?/gi;
  const refs: TableRef[] = [];
  for (const m of statement.matchAll(re)) {
    const first = unquote(m[1]);
    const second = m[2] ? unquote(m[2]) : undefined;
    const aliasRaw = m[3] ? unquote(m[3]) : undefined;
    const alias =
      aliasRaw && !ALIAS_STOPWORDS.has(aliasRaw.toLowerCase())
        ? aliasRaw
        : undefined;
    refs.push(
      second
        ? { schema: first, table: second, alias }
        : { table: first, alias },
    );
  }
  return refs;
}

// Is the cursor in a position that expects a data type?
function isTypeContext(before: string): boolean {
  // DuckDB `expr::TYPE` cast.
  if (/::\s*[\w$]*$/.test(before)) return true;
  // `CAST(expr AS <type>`.
  if (/\bcast\s*\([^()]*\bas\s+[\w$]*$/i.test(before)) return true;
  return false;
}

// The last top-level clause keyword before the cursor maps to a clause group.
function detectClause(before: string): Clause {
  const lower = before.toLowerCase();
  let clause: Clause = "select";
  let found = false;
  const re =
    /\b(from|join|into|update|select|where|having|on|set|by|qualify)\b/gi;
  for (const m of lower.matchAll(re)) {
    found = true;
    const kw = m[1];
    clause =
      kw === "from" || kw === "join" || kw === "into" || kw === "update"
        ? "from"
        : "select";
  }
  return found ? clause : "select";
}

export function getCursorContext(text: string, offset: number): CursorContext {
  const { text: statement, offset: pos } = activeStatementBounds(text, offset);
  const before = statement.slice(0, pos);

  const { word, start } = wordBefore(statement, pos);
  const wordPrefix = word;
  const qualifier = parseQualifier(statement, start);
  const fromTables = referencedTables(statement);

  // Statement start: nothing meaningful typed yet.
  if (before.replace(/[\w$.]+$/, "").trim() === "" && qualifier.length === 0) {
    return { clause: "start", wordPrefix, qualifier, fromTables };
  }

  let clause: Clause;
  if (isTypeContext(before)) {
    clause = "type";
  } else {
    clause = detectClause(before);
  }
  return { clause, wordPrefix, qualifier, fromTables };
}
