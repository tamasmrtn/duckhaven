// Pure, Monaco-free line-diff hunk computation for the AI-proposed-edit
// review flow. Kept separate from SqlEditor.tsx (which renders these hunks
// as inline decorations/view zones) so the actual diffing logic stays fully
// unit-testable — Monaco cannot run in the jsdom test environment.

import { diffLines, type Change } from "diff";

export type HunkStatus = "pending" | "accepted" | "rejected";

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
  status: HunkStatus;
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
          status: "pending",
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
        status: "pending",
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
      status: "pending",
    });
    newLine += addedLineCount;
  }

  return hunks;
}

// Reconstructs a document from `oldSql`/`newSql` and a hunk resolution list:
// each hunk contributes its old-side text if `status === "rejected"`,
// otherwise its new-side text ("pending" and "accepted" both mean "keep the
// live text" — only a reject flips a hunk back to its old side). Accepting
// every hunk reproduces `newSql` exactly; rejecting every hunk reproduces
// `oldSql` exactly.
//
// Re-runs `diffLines` rather than patching Monaco model offsets directly:
// `diffLines` is a pure function of `(oldSql, newSql)`, so this produces the
// exact same chunk sequence — and therefore the exact same hunk pairing — as
// `computeHunks` did, letting the two stay in lockstep by index without
// needing to track per-hunk offsets through subsequent edits.
export function applyHunkResolutions(
  oldSql: string,
  newSql: string,
  hunks: DiffHunk[],
): string {
  const changes: Change[] = diffLines(oldSql, newSql);
  const out: string[] = [];
  let hunkIndex = 0;

  for (let i = 0; i < changes.length; i++) {
    const change = changes[i];

    if (!change.added && !change.removed) {
      out.push(change.value);
      continue;
    }

    if (change.removed) {
      const next = changes[i + 1];
      const hunk = hunks[hunkIndex++];
      const rejected = hunk?.status === "rejected";
      if (next?.added) {
        out.push(rejected ? change.value : next.value);
        i++; // consumed the paired addition
        continue;
      }
      // Pure deletion: restore it when rejected, otherwise leave it out
      // (already its state in `newSql`).
      if (rejected) out.push(change.value);
      continue;
    }

    // Pure insertion.
    const hunk = hunks[hunkIndex++];
    if (hunk?.status !== "rejected") out.push(change.value);
  }

  return out.join("");
}
