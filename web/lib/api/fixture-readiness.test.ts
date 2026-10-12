import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeFixtureReadiness, getFixtureReadiness } from "./fixture-readiness";
const missing = {version: 1, observed_at_utc: "2026-10-12T12:00:00Z", generated_at_utc: null, calendar_retrieved_at_utc: null, calendar_state: "MISSING", reason: "PROJECTION_NOT_CONFIGURED", e0_state: "CALENDAR_ADAPTER_REQUIRED", history_coverage: "NOT_ASSESSED", fixtures: []};
describe("fixture readiness decoder", () => {
  it("accepts an explicit missing state", () => expect(decodeFixtureReadiness(missing)).toEqual(missing));
  it("rejects a fresh empty calendar", () => expect(() => decodeFixtureReadiness({...missing, calendar_state: "FRESH"})).toThrow());
  it("rejects unknown states and malformed timestamps", () => {
    expect(() => decodeFixtureReadiness({...missing, calendar_state: "READY"})).toThrow();
    expect(() => decodeFixtureReadiness({...missing, observed_at_utc: "yesterday"})).toThrow();
  });
});


describe("fixture readiness live request", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
  it.each(["https://example.test/api/v1", "https://example.test/api/v1/"])("uses the configured API base %s once", async base => {
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_MODE", "live");
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_URL", base);
    const fetcher = vi.fn().mockResolvedValue({ok: true, json: async () => missing});
    vi.stubGlobal("fetch", fetcher);
    await expect(getFixtureReadiness()).resolves.toEqual(missing);
    expect(fetcher).toHaveBeenCalledWith("https://example.test/api/v1/fixture-readiness", {cache: "no-store"});
  });
});
