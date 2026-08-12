// Pure, Monaco-free line-diff hunk computation for the AI-proposed-edit
// review flow. Kept separate from SqlEditor.tsx (which renders these hunks
// as inline decorations/view zones) so the actual diffing logic stays fully
// unit-testable — Monaco cannot run in the jsdom test environment.

import { diffLines, type Change } from "diff";

export interface DiffHunk {
  // Stable within one proposal, e.g. "hunk-0".
  id: string;
  // 1-based, inclusive line range in the CURRENT (new) document where this
  // hunk's added content lives. `addStartLine > addEndLine` means a pure
  // deletion — nothing was added at this position.
  addStartLine: number;
  addEndLine: number;
  // The old lines this hunk replaced/removed. Not present in the document —
  // rendered separately (as ghost text) by the caller. Empty for a pure
  // insertion.
  removedLines: string[];
}

// A `diffLines` chunk's `value` is one or more lines joined by "\n", with a
// trailing "\n" unless it's the final chunk of a document with no trailing
// newline. Split back into individual lines, dropping that trailing artifact.
function linesOf(value: string): string[] {
  const lines = value.split("\n");
  if (value.endsWith("\n")) lines.pop();
  return lines;
}

// Line-level diff between `oldSql` and `newSql`, LCS-aligned via `diffLines`,
// coalesced into hunks: an adjacent removed-then-added run is one "replace"
// hunk (the common shape of an AI-proposed edit); a lone removed or added
// run is a deletion-only/insertion-only hunk. Identical inputs yield no
// hunks.
export function computeHunks(oldSql: string, newSql: string): DiffHunk[] {
  const changes: Change[] = diffLines(oldSql, newSql);
  const hunks: DiffHunk[] = [];
  let newLine = 1; // 1-based cursor into the new document
  let nextId = 0;

  for (let i = 0; i < changes.length; i++) {
    const change = changes[i];

    if (!change.added && !change.removed) {
      newLine += linesOf(change.value).length;
      continue;
    }

    if (change.removed) {
      const removedLines = linesOf(change.value);
      const next = changes[i + 1];
      if (next?.added) {
        // Replace: pair this removal with the addition right after it.
        const addedLineCount = linesOf(next.value).length;
        hunks.push({
          id: `hunk-${nextId++}`,
          addStartLine: newLine,
          addEndLine: newLine + addedLineCount - 1,
          removedLines,
        });
        newLine += addedLineCount;
        i++; // consumed the paired addition
        continue;
      }
      // Pure deletion — nothing added at this position in the new document.
      hunks.push({
        id: `hunk-${nextId++}`,
        addStartLine: newLine,
        addEndLine: newLine - 1,
        removedLines,
      });
      continue;
    }

    // Pure insertion (a `removed` chunk immediately before this one would
    // already have consumed it via the `next?.added` branch above).
    const addedLineCount = linesOf(change.value).length;
    hunks.push({
      id: `hunk-${nextId++}`,
      addStartLine: newLine,
      addEndLine: newLine + addedLineCount - 1,
      removedLines: [],
    });
    newLine += addedLineCount;
  }

  return hunks;
}
