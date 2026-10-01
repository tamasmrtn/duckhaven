import { useReducer } from "react";
import { MIN_ZOOM_MS } from "./range";

/**
 * Drag across any chart to zoom to that stretch of time; click a bar (no drag)
 * to select its bucket. One gesture model for every panel, so the stack reads as
 * one instrument.
 *
 * A pure reducer, so the gesture logic is tested without a rendered chart (jsdom
 * gives Recharts no size, so a DOM drag there exercises nothing).
 */
export interface ZoomState {
  // Epoch ms of the pointer-down, and of the pointer's current position.
  anchor: number | null;
  current: number | null;
}

export type ZoomAction =
  | { type: "down"; at: number }
  | { type: "move"; at: number }
  | { type: "cancel" };

export type ZoomOutcome =
  | { kind: "zoom"; from: number; to: number }
  | { kind: "select"; at: number }
  | { kind: "none" };

export const IDLE: ZoomState = { anchor: null, current: null };

export function zoomReducer(state: ZoomState, action: ZoomAction): ZoomState {
  switch (action.type) {
    case "down":
      return { anchor: action.at, current: action.at };
    case "move":
      return state.anchor === null ? state : { ...state, current: action.at };
    case "cancel":
      return IDLE;
  }
}

/**
 * What releasing the pointer means. A drag narrower than the minimum zoom is a
 * click that wobbled, and selects rather than zooming into a sliver the API would
 * refuse.
 */
export function release(state: ZoomState, at: number | null): ZoomOutcome {
  if (state.anchor === null) return { kind: "none" };
  const end = at ?? state.current ?? state.anchor;
  const from = Math.min(state.anchor, end);
  const to = Math.max(state.anchor, end);
  if (to - from >= MIN_ZOOM_MS) return { kind: "zoom", from, to };
  return { kind: "select", at: state.anchor };
}

/** The live drag selection, or null when there is nothing worth drawing. */
export function selection(state: ZoomState): [number, number] | null {
  if (state.anchor === null || state.current === null) return null;
  if (state.anchor === state.current) return null;
  return [
    Math.min(state.anchor, state.current),
    Math.max(state.anchor, state.current),
  ];
}

export function useZoom() {
  return useReducer(zoomReducer, IDLE);
}
