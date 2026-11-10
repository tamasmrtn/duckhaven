/**
 * Two letters for an avatar. Words are split on whitespace, `-` and `_`, so
 * "E2E owner" gives "EO" and "E2E reader" gives "ER" rather than both "E2";
 * a single word falls back to its first two characters.
 */
export function initials(name: string): string {
  const words = name
    .trim()
    .split(/[\s_-]+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

const PROVIDER_LABELS: Record<string, string> = {
  service_account: "Service account",
};

/** The server's `auth_provider` value as prose; never the raw enum. */
export function providerLabel(provider: string): string {
  const known = PROVIDER_LABELS[provider];
  if (known) return known;
  const spaced = provider.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
