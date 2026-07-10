export interface SelectionAnchor {
  text: string;
  start: number;
  end: number;
}

export interface ScopedEditResult {
  sql: string;
  note?: string;
}

/**
 * Apply an assistant-proposed edit to the current worksheet text. When the edit
 * is scoped to a selection, splice the replacement into the anchor's offsets only
 * if the text there still matches what was selected when the request was sent —
 * otherwise the document changed since the request, so fall back to a full
 * replace rather than corrupt it at stale offsets.
 */
export function applyScopedEdit(
  current: string,
  anchor: SelectionAnchor | null,
  newSql: string,
  scoped: boolean,
): ScopedEditResult {
  if (
    scoped &&
    anchor &&
    current.slice(anchor.start, anchor.end) === anchor.text
  ) {
    return {
      sql: current.slice(0, anchor.start) + newSql + current.slice(anchor.end),
    };
  }
  return {
    sql: newSql,
    // Only note a drift when an anchor existed but its text no longer matches at
    // those offsets — not the plain no-selection path (anchor === null), where a
    // full replacement was expected and nothing changed.
    note:
      scoped && anchor !== null
        ? "Applied as a full replacement — the document changed since this was requested."
        : undefined,
  };
}
