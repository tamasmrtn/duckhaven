import type { ChatChartWidget } from "@/types/assistant";

/**
 * A widget for the editor to add to the file as it opens, such as a chart the
 * assistant drew. It travels in the history entry that opens the editor, and
 * `key` tells one hand-off from the next, so each is added once.
 */
export interface AddWidget {
  key: string;
  widget: ChatChartWidget;
}

declare module "@tanstack/react-router" {
  interface HistoryState {
    addWidget?: AddWidget;
  }
}
