import { useEffect } from "react";

export const BASE_TITLE = "DuckHaven";

/**
 * Names the browser tab and the page for assistive tech ("Catalog · DuckHaven").
 * Pass null to leave the title alone. Cleanup returns to the bare product name
 * rather than a remembered previous title, so two pages swapping in one
 * navigation can never leave a stale name behind.
 */
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return;
    document.title = `${title} · ${BASE_TITLE}`;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [title]);
}
