// The page to open after signing in. A signed-out link to a page reaches the
// login page as ?redirect=<path>. Single sign-on leaves the app and lands on
// "/", so the path is kept in session storage for that route to read.
const KEY = "dh-return-to";

/** The path, query and hash of `value` when it is a page of this app. */
export function safeReturnTo(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.startsWith("/")) return undefined;
  let url: URL;
  try {
    url = new URL(value, window.location.origin);
  } catch {
    return undefined;
  }
  // "//host" and "/\host" parse to another origin.
  if (url.origin !== window.location.origin) return undefined;
  return url.pathname + url.search + url.hash;
}

export function keepReturnTo(path: string): void {
  try {
    sessionStorage.setItem(KEY, path);
  } catch {
    // Storage is off: single sign-on opens the first workspace.
  }
}

/** The kept path, removed so that it is used once. */
export function takeReturnTo(): string | undefined {
  try {
    const path = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return safeReturnTo(path);
  } catch {
    return undefined;
  }
}
