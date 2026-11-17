import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useMe, useUpdateMe } from "@/queries/auth";
import type { User } from "@/types/auth";

type PreferenceField = "theme" | "sidebar";

function readCache<V extends string>(
  key: string,
  values: readonly V[],
  fallback: V,
): V {
  try {
    const cached = localStorage.getItem(key);
    return (values as readonly unknown[]).includes(cached)
      ? (cached as V)
      : fallback;
  } catch {
    return fallback;
  }
}

/**
 * A preference saved on the user's account, so it follows them to every
 * browser. This browser's last value paints first, and the account's value
 * replaces it once `/me` answers; a value the app does not know, or none,
 * means `fallback`.
 */
export function useAccountPreference<V extends string>(
  field: PreferenceField,
  values: readonly V[],
  fallback: V,
  cacheKey: string,
): [V, (next: V) => void] {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const { mutate } = useUpdateMe();
  const [value, setValue] = useState<V>(() =>
    readCache(cacheKey, values, fallback),
  );

  // Adopt the account's value whenever it arrives or changes, during render
  // rather than in an effect, so the first paint after `/me` is already right.
  const accountValue = me?.[field];
  const accountKey = me ? `${me.id}:${accountValue ?? ""}` : null;
  const [adoptedKey, setAdoptedKey] = useState<string | null>(null);
  if (me && accountKey !== adoptedKey) {
    setAdoptedKey(accountKey);
    setValue(
      (values as readonly unknown[]).includes(accountValue)
        ? (accountValue as V)
        : fallback,
    );
  }

  useEffect(() => {
    try {
      localStorage.setItem(cacheKey, value);
    } catch {
      // Only the first-paint cache is lost; the account still has it.
    }
  }, [cacheKey, value]);

  const save = useCallback(
    (next: V) => {
      setValue(next);
      // A `/me` read already in flight would answer with the old value.
      void qc.cancelQueries({ queryKey: ["me"] });
      qc.setQueryData<User>(["me"], (user) =>
        user ? { ...user, [field]: next } : user,
      );
      mutate({ [field]: next });
    },
    [qc, mutate, field],
  );

  return [value, save];
}
