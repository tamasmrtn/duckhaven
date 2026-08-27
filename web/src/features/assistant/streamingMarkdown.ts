// A streamed answer is re-parsed from scratch on every token, so an *incomplete*
// markdown construct parses as whatever it currently looks like: a half-typed
// table renders its header cells as literal pipes in the paragraph above it, and
// an unclosed `**` or backtick shows as raw punctuation. The text then relocates
// when the construct closes. These helpers rewrite an in-progress string so it
// parses to the shape it will have when it's finished.
//
// Unclosed *fences* need no such help — CommonMark runs them to the end of the
// document, so a partial body is already inside a code block.

const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})/;
const DELIMITER_ROW_RE = /^\s*\|?[\s:|-]*-[\s:|-]*$/;
// Stands in for a complete inline-code span while emphasis is scanned. The
// sentinel is in the private use area, which a model never emits, so it can't
// collide with the answer's own text.
const SPAN_RE = /\uE000(\d+)\uE000/g;

/** Cells in a table row, ignoring the optional leading and trailing pipes. */
function cellCount(line: string): number {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").length;
}

/** Whether a run of pipe lines is already enough for GFM to build a table. */
function formsTable(run: string[]): boolean {
  return (
    run.length >= 2 &&
    DELIMITER_ROW_RE.test(run[1]) &&
    cellCount(run[1]) === cellCount(run[0])
  );
}

/** Drop the last `delim` if the string holds an odd number of them. */
function dropUnpaired(text: string, delim: string): string {
  const parts = text.split(delim);
  if (parts.length < 2 || parts.length % 2 !== 0) return text;
  return parts.slice(0, -1).join(delim) + parts[parts.length - 1];
}

/**
 * Remove emphasis and code delimiters that are open but not yet closed, keeping
 * the text they wrap. `The **thr` becomes `The thr`, which then grows in place
 * and gains its bold when the closing `**` arrives, instead of flashing raw
 * asterisks. Complete inline-code spans are held aside first, so the emphasis
 * pass never mangles a `SELECT count(*)`. Underscores are left alone — stripping
 * them would break snake_case identifiers, which outnumber `_emphasis_` by far
 * in an answer about SQL.
 */
function stripUnclosedInline(text: string): string {
  const spans: string[] = [];
  let out = text.replace(/`[^`\n]+`/g, (span) => {
    spans.push(span);
    return `\uE000${spans.length - 1}\uE000`;
  });
  // Every complete span is held aside above, so a backtick still here opens
  // something that has not closed — including the `` of a fence being typed,
  // which pairs up as an empty span but is not one.
  out = out.replace(/`/g, "");
  for (const delim of ["**", "*"]) {
    out = dropUnpaired(out, delim);
  }
  return out.replace(SPAN_RE, (_, index: string) => spans[Number(index)]);
}

/**
 * Rewrite in-progress streamed markdown so it renders as the shape it will
 * settle into. Only ever called while a turn is streaming — the persisted
 * transcript renders its text verbatim.
 */
export function stabilizeStreamingMarkdown(text: string): string {
  const lines = text.split("\n");
  const endsWithNewline = text.endsWith("\n");
  // The trailing "" a final newline produces isn't a line the rules consider.
  const end = endsWithNewline ? lines.length - 1 : lines.length;

  let openFence: string | null = null;
  let openFenceLine = -1;
  let lastFenceCloseLine = -1;
  for (let i = 0; i < end; i++) {
    const match = FENCE_RE.exec(lines[i]);
    if (!match) continue;
    if (openFence === null) {
      openFence = match[1];
      openFenceLine = i;
    } else if (
      match[1][0] === openFence[0] &&
      match[1].length >= openFence.length
    ) {
      openFence = null;
      lastFenceCloseLine = i;
    }
  }

  if (openFence !== null) {
    // The opening fence is itself still being typed, so it would render as an
    // empty code block: hold it back until it has a body.
    if (openFenceLine === end - 1 && !endsWithNewline) {
      return lines.slice(0, openFenceLine).join("\n");
    }
    // Everything past the fence is code. Leave it as it is — and in particular
    // don't run the emphasis pass over a code body.
    return text;
  }

  // A trailing run of pipe lines GFM can't build a table from yet would
  // otherwise render as pipes appended to whatever came before it.
  let runStart = end;
  while (runStart > 0 && /^\s*\|/.test(lines[runStart - 1])) runStart--;
  if (runStart < end && !formsTable(lines.slice(runStart, end))) {
    return lines.slice(0, runStart).join("\n");
  }

  // Unclosed emphasis, scoped to the trailing paragraph and never inside a fence.
  let paraStart = lastFenceCloseLine + 1;
  for (let i = end - 1; i > paraStart; i--) {
    if (lines[i].trim() === "") {
      paraStart = i + 1;
      break;
    }
  }
  if (paraStart >= end) return text;
  return [
    ...lines.slice(0, paraStart),
    stripUnclosedInline(lines.slice(paraStart).join("\n")),
  ].join("\n");
}
