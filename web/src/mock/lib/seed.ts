// Deterministic generators that replace Math.random() / Date.now()-based IDs and
// sample data, so mocked responses are stable across runs and test isolation holds.

let counter = 0;

// Monotonic id for resources created at runtime (POST handlers). Reset per test.
export function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-new-${counter}`;
}

// Deterministic bootstrap token, shaped like the backend's `dh_boot_<token>`.
export function nextBootstrapToken(): string {
  counter += 1;
  return `dh_boot_seed${String(counter).padStart(12, "0")}`;
}

export function resetSeed(): void {
  counter = 0;
}

// Deterministic cell value keyed by column type + row index. Pure: no state to
// reset. Mirrors the small DuckDB scalar set plus the catalog's UUID/JSON columns.
export function seededCell(
  col: { name: string; type: string },
  i: number,
): unknown {
  switch (col.type.toUpperCase()) {
    case "UUID":
      return `${col.name}-${String(i).padStart(8, "0")}`;
    case "BIGINT":
    case "INTEGER":
      return i * 1000 + col.name.length;
    case "DOUBLE":
    case "DECIMAL":
      return +(((i * 37) % 1000) / 7).toFixed(2);
    case "BOOLEAN":
      return i % 2 === 0;
    case "TIMESTAMP":
      return new Date(Date.UTC(2026, 0, 1) - i * 86400000).toISOString();
    case "DATE":
      return new Date(Date.UTC(2026, 0, 1) - i * 86400000)
        .toISOString()
        .slice(0, 10);
    case "JSON":
      return JSON.stringify({ action: "click", target: `btn-${i}` });
    default:
      return i % 3 === 0 ? null : `${col.name}-${i}`;
  }
}
