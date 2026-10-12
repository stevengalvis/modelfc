import { describe, expect, it } from "vitest";
import { decodeFixtureReadiness } from "./fixture-readiness";
const missing = {version: 1, observed_at_utc: "2026-10-12T12:00:00Z", generated_at_utc: null, calendar_retrieved_at_utc: null, calendar_state: "MISSING", reason: "PROJECTION_NOT_CONFIGURED", e0_state: "CALENDAR_ADAPTER_REQUIRED", history_coverage: "NOT_ASSESSED", fixtures: []};
describe("fixture readiness decoder", () => {
  it("accepts an explicit missing state", () => expect(decodeFixtureReadiness(missing)).toEqual(missing));
  it("rejects a fresh empty calendar", () => expect(() => decodeFixtureReadiness({...missing, calendar_state: "FRESH"})).toThrow());
  it("rejects unknown states and malformed timestamps", () => {
    expect(() => decodeFixtureReadiness({...missing, calendar_state: "READY"})).toThrow();
    expect(() => decodeFixtureReadiness({...missing, observed_at_utc: "yesterday"})).toThrow();
  });
});
