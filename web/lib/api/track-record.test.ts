import { describe, expect, it } from "vitest";
import fixture from "../../../tests/fixtures/track_record.json";
import { decodeTrackRecord, emptyTrackRecord } from "./track-record";

describe("published record contract", () => {
  it("accepts the real offline backend fixture and truthful empty state", () => {
    expect(decodeTrackRecord(fixture).summary.picks).toBeGreaterThan(0);
    expect(decodeTrackRecord(emptyTrackRecord).summary.roi).toBeNull();
  });
  it("rejects inflated counts, duplicate picks, invalid chronology and losses for pending", () => {
    for (const change of [
      (v: typeof fixture) => { v.summary.picks++; },
      (v: typeof fixture) => { v.publications.push(v.publications[0]); },
      (v: typeof fixture) => { v.publications[0].published_at_utc = v.publications[0].offer.kickoff_utc; },
      (v: typeof fixture) => { Object.assign(v.publications[0], { profit_units: -1 }); },
      (v: typeof fixture) => { v.by_view.higher_probability.picks++; },
    ]) {
      const value = structuredClone(fixture); change(value);
      expect(() => decodeTrackRecord(value)).toThrow();
    }
  });
});

it("accepts saved BTTS odds and rejects duplicate slots, invalid EV and pushes", async () => {
  const fixture = (await import("../../../tests/fixtures/btts_track_record.json")).default;
  expect(decodeTrackRecord(fixture).summary.picks).toBe(1);
  for (const mutate of [
    (v: typeof fixture) => { v.publications.push(v.publications[0]); },
    (v: typeof fixture) => { v.publications[0].offer.expected_profit = 0; },
    (v: typeof fixture) => { v.publications[0].result = "PUSH"; },
    (v: typeof fixture) => { v.publications[0].offer.no_vig_probability_edge = .04; },
  ]) {
    const copy = structuredClone(fixture); mutate(copy);
    expect(() => decodeTrackRecord(copy)).toThrow();
  }
});
