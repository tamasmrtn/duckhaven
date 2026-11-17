import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { NAV_MODE_CACHE_KEY } from "@/queries/auth";
import { NAV_MODES, type NavMode } from "@/types/auth";
import { useAccountPreference } from "./useAccountPreference";

export const DEFAULT_NAV_MODE: NavMode = "expanded";

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
  // An account that never chose gets the default, not whatever this browser
  // last showed someone else.
  const [mode, setMode] = useAccountPreference(
    "sidebar",
    NAV_MODES,
    DEFAULT_NAV_MODE,
    NAV_MODE_CACHE_KEY,
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
