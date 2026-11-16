import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NAV_MODE_CACHE_KEY, useMe, useUpdateMe } from "@/queries/auth";
import { NAV_MODES, type NavMode, type User } from "@/types/auth";

export const DEFAULT_NAV_MODE: NavMode = "expanded";

function isNavMode(value: unknown): value is NavMode {
  return (NAV_MODES as readonly unknown[]).includes(value);
}

function readCache(): NavMode {
  try {
    const cached = localStorage.getItem(NAV_MODE_CACHE_KEY);
    return isNavMode(cached) ? cached : DEFAULT_NAV_MODE;
  } catch {
    return DEFAULT_NAV_MODE;
  }
}

interface NavModeContextValue {
  mode: NavMode;
  setMode: (mode: NavMode) => void;
  /** Expanded becomes collapsed; collapsed or expand-on-hover becomes expanded. */
  toggle: () => void;
}

const NavModeContext = createContext<NavModeContextValue | null>(null);

/**
 * How the main navigation is laid out, saved on the user's account so it
 * follows them to every browser. One shared instance, so the toggle, ⌘B and
 * Settings never disagree.
 */
export function NavModeProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const { mutate } = useUpdateMe();
  const [mode, setLocalMode] = useState<NavMode>(readCache);

  // Adopt the account's choice whenever it arrives or changes. Null means the
  // user never chose, so they get the default rather than whatever this
  // browser last showed someone else.
  const accountKey = me ? `${me.id}:${me.sidebar ?? ""}` : null;
  const [adoptedKey, setAdoptedKey] = useState<string | null>(null);
  if (me && accountKey !== adoptedKey) {
    setAdoptedKey(accountKey);
    setLocalMode(isNavMode(me.sidebar) ? me.sidebar : DEFAULT_NAV_MODE);
  }

  useEffect(() => {
    try {
      localStorage.setItem(NAV_MODE_CACHE_KEY, mode);
    } catch {
      // Only the first-paint cache is lost; the account still has it.
    }
  }, [mode]);

  const setMode = useCallback(
    (next: NavMode) => {
      setLocalMode(next);
      // A `/me` read already in flight would answer with the old choice.
      void qc.cancelQueries({ queryKey: ["me"] });
      qc.setQueryData<User>(["me"], (user) =>
        user ? { ...user, sidebar: next } : user,
      );
      mutate({ sidebar: next });
    },
    [qc, mutate],
  );

  const toggle = useCallback(
    () => setMode(mode === "expanded" ? "collapsed" : "expanded"),
    [mode, setMode],
  );

  const value = useMemo<NavModeContextValue>(
    () => ({ mode, setMode, toggle }),
    [mode, setMode, toggle],
  );

  return (
    <NavModeContext.Provider value={value}>{children}</NavModeContext.Provider>
  );
}

export function useNavMode(): NavModeContextValue {
  const ctx = useContext(NavModeContext);
  if (!ctx) {
    throw new Error("useNavMode must be used within a NavModeProvider");
  }
  return ctx;
}
