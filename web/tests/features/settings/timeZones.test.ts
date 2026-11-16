import { afterEach, describe, expect, it } from "vitest";
import {
  browserZone,
  currentName,
  offsetOf,
  todayIn,
  zoneGroups,
  zoneIds,
  zoneOption,
  zoneScore,
} from "@/features/settings/timeZones";

const WINTER = new Date("2026-01-15T12:00:00Z");
const SUMMER = new Date("2026-07-15T12:00:00Z");
const TZ = process.env.TZ;

afterEach(() => {
  process.env.TZ = TZ;
});

describe("timeZones", () => {
  it("offers UTC first and every zone once, by its current name", () => {
    const ids = zoneIds();
    expect(ids[0]).toBe("UTC");
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain("Asia/Kolkata");
    expect(ids).toContain("Europe/Kyiv");
    expect(ids).not.toContain("Asia/Calcutta");
    expect(ids).not.toContain("Europe/Kiev");
  });

  it("names a renamed zone by its current name", () => {
    expect(currentName("Asia/Calcutta")).toBe("Asia/Kolkata");
    expect(currentName("Europe/Amsterdam")).toBe("Europe/Amsterdam");
  });

  it("reads the browser's zone by its current name", () => {
    process.env.TZ = "Asia/Calcutta";
    expect(browserZone()).toBe("Asia/Kolkata");
    process.env.TZ = "Europe/Amsterdam";
    expect(browserZone()).toBe("Europe/Amsterdam");
  });

  it("gives the offset in force at that moment", () => {
    expect(offsetOf("Europe/Amsterdam", WINTER)).toBe("UTC+01:00");
    expect(offsetOf("Europe/Amsterdam", SUMMER)).toBe("UTC+02:00");
    expect(offsetOf("Asia/Kathmandu", WINTER)).toBe("UTC+05:45");
    expect(offsetOf("America/Los_Angeles", WINTER)).toBe("UTC-08:00");
    expect(offsetOf("UTC", WINTER)).toBe("UTC+00:00");
    expect(offsetOf("Not/A_Zone", WINTER)).toBe("");
  });

  it("can be found by city, abbreviation, former name and every spelling of its offset", () => {
    const india = zoneOption("Asia/Kolkata", WINTER);
    expect(india).toMatchObject({
      city: "Kolkata",
      region: "Asia",
      offset: "UTC+05:30",
    });
    expect(india.keywords).toEqual(
      expect.arrayContaining([
        "India Standard Time",
        "+05:30",
        "+5:30",
        "UTC+5:30",
        "GMT+5:30",
        "Asia/Calcutta",
      ]),
    );
    const la = zoneOption("America/Los_Angeles", WINTER);
    expect(la.city).toBe("Los Angeles");
    expect(la.keywords).toEqual(
      expect.arrayContaining(["PST", "-8", "UTC-8", "GMT-8", "-08:00"]),
    );
  });

  it("splits a nested name into its city and full region", () => {
    expect(zoneOption("America/Argentina/Buenos_Aires", WINTER)).toMatchObject({
      city: "Buenos Aires",
      region: "America/Argentina",
    });
    expect(zoneOption("UTC", WINTER)).toMatchObject({ city: "UTC", region: "" });
  });

  it("groups by top-level region, UTC first, regions and cities in order", () => {
    const groups = zoneGroups(WINTER, [
      "Europe/Paris",
      "UTC",
      "America/Argentina/Cordoba",
      "Europe/Amsterdam",
      "America/Chicago",
    ]);
    expect(groups.map((g) => g.region)).toEqual(["", "America", "Europe"]);
    expect(groups[1].zones.map((z) => z.id)).toEqual([
      "America/Chicago",
      "America/Argentina/Cordoba",
    ]);
    expect(groups[2].zones.map((z) => z.city)).toEqual(["Amsterdam", "Paris"]);
  });

  it("filters by the start of a word, city first", () => {
    const score = (id: string, search: string) =>
      zoneScore(id, search, zoneOption(id, WINTER).keywords);
    expect(score("Asia/Kolkata", "kolk")).toBe(1);
    expect(score("America/New_York", "new york")).toBe(1);
    expect(score("America/New_York", "york")).toBe(0.5);
    expect(score("America/New_York", "eastern")).toBe(0.5);
    expect(score("Asia/Kolkata", "calcutta")).toBe(0.5);
    expect(score("Europe/Amsterdam", "europe/amst")).toBe(0.5);
    expect(score("Asia/Kolkata", "")).toBe(1);
    // A letter-by-letter match is not one.
    expect(score("America/Kentucky/Louisville", "kolkata")).toBe(0);
    expect(score("America/North_Dakota/Beulah", "kolkata")).toBe(0);
    expect(score("Asia/Kolkata", "olkata")).toBe(0);
    // Searching "utc" finds UTC, not every zone whose offset is spelled UTC±.
    expect(score("UTC", "utc")).toBe(1);
    expect(score("Europe/Amsterdam", "utc")).toBe(0);
  });

  it("matches an offset search only to that exact offset", () => {
    const score = (id: string, search: string) =>
      zoneScore(id, search, zoneOption(id, WINTER).keywords);
    for (const search of ["+1", "+01:00", "UTC+1", "utc+01:00", "GMT+1", " + 1 "])
      expect(score("Europe/Amsterdam", search), search).toBe(1);
    expect(score("Pacific/Port_Moresby", "+1")).toBe(0);
    expect(score("Asia/Kolkata", "+5:30")).toBe(1);
    expect(score("Asia/Kolkata", "+5")).toBe(0);
    expect(score("America/Los_Angeles", "-8")).toBe(1);
    expect(score("America/Los_Angeles", "+8")).toBe(0);
  });

  it("says which date it is in the zone, across the date line", () => {
    const late = new Date("2026-11-16T23:30:00Z");
    expect(todayIn("UTC", late)).toBe("Mon 16 Nov 2026");
    expect(todayIn("Pacific/Auckland", late)).toBe("Tue 17 Nov 2026");
    expect(todayIn("Not/A_Zone", late)).toBe("");
  });
});
