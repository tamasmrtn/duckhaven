import { useCallback, useEffect, useState } from "react";

function read(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((v): v is string => typeof v === "string")
        : [],
    );
  } catch {
    return new Set();
  }
}

/**
 * A set of strings kept in this browser's localStorage under `key`: a
 * per-viewer convenience (which tree nodes are expanded) that survives
 * reloads. Storage that is unavailable or full degrades to in-memory only.
 */
export function usePersistedSet(key: string) {
  const [values, setValues] = useState<Set<string>>(() => read(key));

  // A different key (another workspace) starts from its own saved set.
  const [loadedKey, setLoadedKey] = useState(key);
  if (loadedKey !== key) {
    setLoadedKey(key);
    setValues(read(key));
  }

  useEffect(() => {
    try {
      if (values.size) localStorage.setItem(key, JSON.stringify([...values]));
      else localStorage.removeItem(key);
    } catch {
      // Only the remembered state is lost.
    }
  }, [key, values]);

  const has = useCallback((value: string) => values.has(value), [values]);

  const toggle = useCallback((value: string) => {
    setValues((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  const clear = useCallback(() => setValues(new Set()), []);

  return { has, toggle, clear, size: values.size };
}
