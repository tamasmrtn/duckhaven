/**
 * The time zones a workspace can be set to, as the picker shows and searches them.
 *
 * The list is the browser's own (`Intl.supportedValuesOf`), so it follows the
 * IANA database the browser ships. Each zone is named by its IANA name, the
 * thing the server stores, so daylight saving is the database's business, and
 * described by its city, region and offset now, which is how people look for
 * one: by the city they are in, or by "+1".
 */

/**
 * Zones ICU still names by what IANA has since renamed them from. Chrome,
 * Edge, Safari and Node use ICU, so India's zone there is `Asia/Calcutta`;
 * Firefox has the current names. The server takes either, as both are in the
 * IANA database; the picker shows and saves the current one, so a person in
 * Kyiv finds it by searching "Kyiv".
 */
const RENAMED: Record<string, string> = {
  "Africa/Asmera": "Africa/Asmara",
  "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
  "America/Catamarca": "America/Argentina/Catamarca",
  "America/Coral_Harbour": "America/Atikokan",
  "America/Cordoba": "America/Argentina/Cordoba",
  "America/Godthab": "America/Nuuk",
  "America/Indianapolis": "America/Indiana/Indianapolis",
  "America/Jujuy": "America/Argentina/Jujuy",
  "America/Louisville": "America/Kentucky/Louisville",
  "America/Mendoza": "America/Argentina/Mendoza",
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Rangoon": "Asia/Yangon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Atlantic/Faeroe": "Atlantic/Faroe",
  "Europe/Kiev": "Europe/Kyiv",
  "Pacific/Enderbury": "Pacific/Kanton",
  "Pacific/Ponape": "Pacific/Pohnpei",
  "Pacific/Truk": "Pacific/Chuuk",
};
const FORMER = Object.fromEntries(
  Object.entries(RENAMED).map(([old, current]) => [current, old]),
);

/** A zone's current IANA name, for one the browser names the old way. */
export function currentName(zone: string): string {
  return RENAMED[zone] ?? zone;
}

/** The browser's own time zone, by its current name; null if it reports none. */
export function browserZone(): string | null {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return zone ? currentName(zone) : null;
}

// Both are ES2022; the web build's lib is ES2020.
type ZoneNameStyle = "longOffset" | "short" | "long";
const supportedValuesOf = (
  Intl as { supportedValuesOf?: (key: "timeZone") => string[] }
).supportedValuesOf;

/** Every zone the picker offers: UTC first, then the browser's list. */
export function zoneIds(): string[] {
  const listed = supportedValuesOf ? supportedValuesOf("timeZone") : [];
  return [...new Set(["UTC", ...listed.map(currentName)])];
}

function part(zone: string, at: Date, style: ZoneNameStyle) {
  const options = { timeZone: zone, timeZoneName: style };
  try {
    return (
      new Intl.DateTimeFormat("en-US", options as Intl.DateTimeFormatOptions)
        .formatToParts(at)
        .find((p) => p.type === "timeZoneName")?.value ?? ""
    );
  } catch {
    // A name this browser does not know: shown as it is, without an offset.
    return "";
  }
}

/** The zone's offset at ``at``, as "UTC+05:30"; empty for a zone the browser does not know. */
export function offsetOf(zone: string, at: Date): string {
  const offset = part(zone, at, "longOffset");
  if (!offset) return "";
  // "GMT" alone is the zero offset.
  return offset === "GMT" ? "UTC+00:00" : offset.replace("GMT", "UTC");
}

export interface ZoneOption {
  /** The IANA name, as the server stores it. */
  id: string;
  city: string;
  /** "Europe", or "America/Argentina"; empty for UTC. */
  region: string;
  offset: string;
  /** What a person may search for, city first: its region, long name, abbreviation and offset spellings. */
  keywords: string[];
}

/** A search that names an offset: "+1", "-08:00", "UTC+5:30", "GMT-3". */
const OFFSET_SEARCH = /^(?:utc|gmt)?\s*([+-]\s*\d.*)$/;
/** A keyword that spells an offset, which only an offset search matches. */
const OFFSET_KEYWORD = /^(?:utc|gmt)?[+-]/;

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * How well a zone answers a search, for the picker's filter: 1 when its city
 * starts with it, 0.5 when a word of its name or keywords does, 0 otherwise.
 * An offset search matches only that exact offset, so "+1" does not find
 * "+10". ``keywords`` starts with the city, as ``zoneOption`` builds it.
 */
export function zoneScore(
  value: string,
  search: string,
  keywords: string[] = [],
): number {
  const query = search.trim().toLowerCase().replace(/_/g, " ");
  if (!query) return 1;
  const words = [value, ...keywords].map((w) =>
    w.toLowerCase().replace(/_/g, " "),
  );
  const offset = OFFSET_SEARCH.exec(query);
  if (offset) {
    const bare = offset[1].replace(/\s+/g, "");
    return words.some((w) => w === bare || w === `utc${bare}`) ? 1 : 0;
  }
  if (keywords[0]?.toLowerCase().replace(/_/g, " ").startsWith(query)) return 1;
  const atWordStart = new RegExp(`(^|[\\s/(-])${escapeRegExp(query)}`);
  return words.some((w) => !OFFSET_KEYWORD.test(w) && atWordStart.test(w))
    ? 0.5
    : 0;
}

export function zoneOption(id: string, at: Date): ZoneOption {
  const segments = id.split("/");
  const city = segments[segments.length - 1].replace(/_/g, " ");
  const region = segments.slice(0, -1).join("/");
  const offset = offsetOf(id, at);
  const keywords = [
    city,
    region,
    offset,
    part(id, at, "long"),
    part(id, at, "short"),
  ];
  const sign = /([+-])(\d\d):(\d\d)/.exec(offset);
  if (sign) {
    const [, s, h, m] = sign;
    const hours = String(Number(h));
    const short = m === "00" ? `${s}${hours}` : `${s}${hours}:${m}`;
    // "+1", "UTC+1", "GMT+1", "+01:00" all find Amsterdam in winter.
    keywords.push(`${s}${h}:${m}`, short, `UTC${short}`, `GMT${short}`);
  }
  if (FORMER[id]) keywords.push(FORMER[id], FORMER[id].replace(/_/g, " "));
  return { id, city, region, offset, keywords: keywords.filter(Boolean) };
}

/** The zones grouped by region, regions and cities in alphabetical order, UTC apart. */
export function zoneGroups(
  at: Date,
  ids: string[] = zoneIds(),
): { region: string; zones: ZoneOption[] }[] {
  const byRegion = new Map<string, ZoneOption[]>();
  for (const id of ids) {
    const option = zoneOption(id, at);
    const region = option.region.split("/")[0];
    const list = byRegion.get(region) ?? [];
    list.push(option);
    byRegion.set(region, list);
  }
  return [...byRegion.entries()]
    .sort(([a], [b]) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)))
    .map(([region, zones]) => ({
      region,
      zones: zones.sort((a, b) => a.city.localeCompare(b.city)),
    }));
}

/** The date it is in ``zone`` at ``at``, as "Mon 16 Nov 2026". */
export function todayIn(zone: string, at: Date): string {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
    })
      .format(at)
      .replace(",", "");
  } catch {
    return "";
  }
}
