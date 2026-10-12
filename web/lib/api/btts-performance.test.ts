import { describe, expect, it, vi, afterEach } from "vitest";
import { createApiClient } from "./client";
import { decodeBttsPerformance, emptyBttsPerformance } from "./btts-performance";
import fixture from "../../../tests/fixtures/btts_performance.json";

afterEach(() => vi.unstubAllGlobals());
describe("BTTS performance contract", () => {
  it("validates Python output and explicit missing samples", () => {
    expect(decodeBttsPerformance(fixture).metrics.sample_count).toBe(1);
    expect(decodeBttsPerformance(emptyBttsPerformance).metrics.brier_score).toBeNull();
  });
  it.each([
    (v: typeof fixture) => { v.unique_fixtures = 2; },
    (v: typeof fixture) => { v.forecasts.push(structuredClone(v.forecasts[0])); },
    (v: typeof fixture) => { v.forecasts[0].actual_btts = "NO"; },
    (v: typeof fixture) => { v.forecasts[0].frozen_at_utc = v.forecasts[0].kickoff_utc; },
    (v: typeof fixture) => { v.forecasts[0].selected_for_metrics = false; },
    (v: typeof fixture) => { v.metrics.brier_score = 0; },
    (v: typeof fixture) => { v.metrics.log_loss = -1; },
    (v: typeof fixture) => { v.metrics.calibration[0].sample_count = 38; },
    (v: typeof fixture) => { v.metrics.calibration[0].observed_frequency = 0; },
    (v: typeof fixture) => { v.forecasts[0].yes_probability = NaN; },
  ])("rejects inconsistent samples, leakage and metrics", mutate => {
    const value = structuredClone(fixture); mutate(value);
    expect(() => decodeBttsPerformance(value)).toThrow();
  });
  it("uses read-only no-store API", async () => {
    const fetcher = vi.fn().mockResolvedValue({ok:true,json:async () => fixture});
    vi.stubGlobal("fetch",fetcher);
    await createApiClient("live", "https://example.test/api/v1").bttsPerformance();
    expect(fetcher).toHaveBeenCalledWith("https://example.test/api/v1/research/btts/performance", expect.objectContaining({cache:"no-store"}));
  });
});
