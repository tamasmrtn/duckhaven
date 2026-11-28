/**
 * Where to put labels beside the ends of lines so none overlap: each as close
 * to its line's end as it can be, at least `gap` pixels from the next, and in
 * order, so a label never crosses another to reach its line.
 *
 * It is the least-squares fit under that constraint, found by isotonic
 * regression (pool adjacent violators): labels that would collide share one
 * block, centred on the mean of where they want to be.
 *
 * `wanted` is in any order; the result is in the same order. Null when the
 * labels cannot fit between `lo` and `hi` at that gap.
 */
export function placeLabels(
  wanted: number[],
  gap: number,
  [lo, hi]: [number, number],
): number[] | null {
  const n = wanted.length;
  if (n === 0) return [];
  if ((n - 1) * gap > hi - lo) return null;
  const order = wanted
    .map((y, i) => [y, i] as const)
    .sort((a, b) => a[0] - b[0] || a[1] - b[1])
    .map(([, i]) => i);

  // With q_k = p_k - k·gap, "at least gap apart" becomes "q never falls", and
  // the closest such q to z_k = wanted_k - k·gap is its isotonic regression.
  const blocks: { sum: number; count: number }[] = [];
  order.forEach((i, k) => {
    blocks.push({ sum: wanted[i] - k * gap, count: 1 });
    while (blocks.length > 1) {
      const last = blocks[blocks.length - 1];
      const before = blocks[blocks.length - 2];
      if (before.sum / before.count <= last.sum / last.count) break;
      before.sum += last.sum;
      before.count += last.count;
      blocks.pop();
    }
  });

  const placed = new Array<number>(n);
  const top = hi - (n - 1) * gap;
  let k = 0;
  for (const block of blocks) {
    const q = Math.min(top, Math.max(lo, block.sum / block.count));
    for (let j = 0; j < block.count; j++, k++) {
      placed[order[k]] = q + k * gap;
    }
  }
  return placed;
}
