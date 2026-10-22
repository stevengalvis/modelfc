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

it("sends scope and cutoff to the API and rejects mismatched responses", async () => {
  const asOf = "2026-10-19T00:00:00Z";
  const report = {...structuredClone(fixture), competition:"ALL", coverage:"PARTIAL"};
  const fetcher = vi.fn().mockResolvedValue({ok:true,json:async () => report});
  vi.stubGlobal("fetch",fetcher);
  const client = createApiClient("live", "https://example.test/api/v1");
  const result = await client.bttsPerformance(undefined, {competition:"ALL", asOf});
  expect(result.metrics).toEqual(fixture.metrics);
  const url = new URL(fetcher.mock.calls[0][0]);
  expect(url.searchParams.get("competition")).toBe("ALL");
  expect(url.searchParams.get("as_of")).toBe(asOf);
  await expect(client.bttsPerformance(undefined,{competition:"E0",asOf})).rejects.toThrow(/filters/);
  await expect(client.bttsPerformance(undefined,{competition:"ALL",asOf:"2026-10-18T00:00:00Z"})).rejects.toThrow(/filters/);
});

it("requires explicit unavailable EPL and partial overall coverage without manufactured samples", () => {
  const empty = {...structuredClone(emptyBttsPerformance), competition:"E0", coverage:"UNSUPPORTED"};
  expect(decodeBttsPerformance(empty).metrics.log_loss).toBeNull();
  expect(() => decodeBttsPerformance({...empty, coverage:"SUPPORTED"})).toThrow();
  expect(() => decodeBttsPerformance({...fixture, competition:"E0", coverage:"UNSUPPORTED"})).toThrow();
  expect(() => decodeBttsPerformance({...fixture, competition:"ALL", coverage:"SUPPORTED"})).toThrow();
  expect(() => decodeBttsPerformance({...fixture, as_of_utc:"2026-09-19T11:59:59Z"})).toThrow();
  expect(() => decodeBttsPerformance({...fixture, as_of_utc:"2026-10-12T00:00:00Z"})).toThrow();
});
