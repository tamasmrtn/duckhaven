// Statement-aware SQL splitting for the worksheet editor's Run command.
// Splits a body on top-level `;`, ignoring semicolons inside single-quoted
// strings (with `''` escaping), double-quoted identifiers, line comments
// (`-- … \n`) and block comments (`/* … */`). Dollar-quoted strings are not
// handled — out of scope for the worksheet editor.
//
// Kept pure and Monaco-free so it can be unit-tested (Monaco is stubbed in tests).

interface Range {
  start: number;
  end: number; // exclusive; just past the terminating `;` (or text length)
}

// Tile the SQL into segments separated by top-level semicolons. Each segment's
// range covers from after the previous `;` up to and including its own `;` (or
// the end of the string for the trailing segment).
function statementRanges(sql: string): Range[] {
  const ranges: Range[] = [];
  const n = sql.length;
  let segStart = 0;
  let i = 0;

  while (i < n) {
    const ch = sql[i];
    const next = sql[i + 1];

    if (ch === "'" || ch === '"') {
      // Quoted string / identifier; a doubled quote is an escaped quote.
      i++;
      while (i < n) {
        if (sql[i] === ch) {
          if (sql[i + 1] === ch) {
            i += 2;
            continue;
          }
          i++;
          break;
        }
        i++;
      }
      continue;
    }

    if (ch === "-" && next === "-") {
      i += 2;
      while (i < n && sql[i] !== "\n") i++;
      continue;
    }

    if (ch === "/" && next === "*") {
      i += 2;
      while (i < n && !(sql[i] === "*" && sql[i + 1] === "/")) i++;
      i += 2; // skip the closing `*/` (overshooting past n is harmless)
      continue;
    }

    if (ch === ";") {
      ranges.push({ start: segStart, end: i + 1 });
      i++;
      segStart = i;
      continue;
    }

    i++;
  }

  // Trailing segment after the last `;` (or the whole string when there is none).
  if (segStart < n) ranges.push({ start: segStart, end: n });

  return ranges;
}

// Trim a raw segment and drop its trailing semicolon.
function cleanStatement(raw: string): string {
  const trimmed = raw.trim();
  return trimmed.endsWith(";") ? trimmed.slice(0, -1).trim() : trimmed;
}

// Split a SQL body into its individual statements, dropping blank ones.
export function splitStatements(sql: string): string[] {
  return statementRanges(sql)
    .map((r) => cleanStatement(sql.slice(r.start, r.end)))
    .filter((s) => s.length > 0);
}

// Blank out the *contents* of single-quoted string literals and comments with
// spaces, preserving every other character (including double-quoted
// identifiers, which stay untouched so their real text is still readable) and
// the string's length/offsets exactly. Used to keep keyword/table-reference
// scanning in the completion engine from misfiring on keyword-shaped text
// sitting inside a string literal or a comment (e.g. `WHERE msg = 'select from
// users'` must not be read as introducing a FROM clause). Mirrors
// `statementRanges`'s quote/comment state walk above, adapted to masking
// instead of `;`-boundary tracking.
export function maskLiteralsAndComments(sql: string): string {
  const n = sql.length;
  const out: string[] = [];
  let i = 0;

  while (i < n) {
    const ch = sql[i];
    const next = sql[i + 1];

    if (ch === "'") {
      // Single-quoted string literal; a doubled quote is an escaped quote.
      // The opening/closing quotes stay, only the interior is blanked.
      out.push("'");
      i++;
      while (i < n) {
        if (sql[i] === "'") {
          if (sql[i + 1] === "'") {
            out.push("  ");
            i += 2;
            continue;
          }
          out.push("'");
          i++;
          break;
        }
        out.push(" ");
        i++;
      }
      continue;
    }

    if (ch === '"') {
      // Double-quoted identifier — real identifier text, not a value
      // literal. Left untouched so unquoting/reference parsing still works.
      out.push('"');
      i++;
      while (i < n) {
        out.push(sql[i]);
        if (sql[i] === '"') {
          if (sql[i + 1] === '"') {
            out.push('"');
            i += 2;
            continue;
          }
          i++;
          break;
        }
        i++;
      }
      continue;
    }

    if (ch === "-" && next === "-") {
      out.push("  ");
      i += 2;
      while (i < n && sql[i] !== "\n") {
        out.push(" ");
        i++;
      }
      continue;
    }

    if (ch === "/" && next === "*") {
      out.push("  ");
      i += 2;
      while (i < n && !(sql[i] === "*" && sql[i + 1] === "/")) {
        out.push(" ");
        i++;
      }
      if (i < n) {
        out.push("  ");
        i += 2;
      }
      continue;
    }

    out.push(ch);
    i++;
  }

  return out.join("");
}

// The raw statement segment enclosing `offset`, with the cursor re-based to that
// segment. Unlike `activeStatement` this keeps the text un-trimmed so offsets
// stay aligned — used by the completion engine for cursor-context detection.
export function activeStatementBounds(
  sql: string,
  offset: number,
): { text: string; offset: number } {
  const ranges = statementRanges(sql);
  if (ranges.length === 0) return { text: "", offset: 0 };
  const clamped = Math.max(0, Math.min(offset, sql.length));
  const seg =
    ranges.find((r) => clamped >= r.start && clamped < r.end) ??
    ranges[ranges.length - 1];
  return { text: sql.slice(seg.start, seg.end), offset: clamped - seg.start };
}

// The single statement enclosing `offset` (the cursor). A cursor sitting just
// after a `;` resolves to the next statement; one in a blank/whitespace-only
// segment yields "".
export function activeStatement(sql: string, offset: number): string {
  const ranges = statementRanges(sql);
  if (ranges.length === 0) return "";
  const clamped = Math.max(0, Math.min(offset, sql.length));
  const seg =
    ranges.find((r) => clamped >= r.start && clamped < r.end) ??
    ranges[ranges.length - 1];
  return cleanStatement(sql.slice(seg.start, seg.end));
}
