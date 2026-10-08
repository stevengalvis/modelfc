import { afterEach, describe, expect, it, vi } from "vitest";
import { BTTS_COMPETITIONS, decodeBttsResearch, isBttsCompetition } from "./btts-research";
import { createApiClient } from "./client";
import { mockBttsResearch } from "./mock-btts-research";
import type { BttsCompetition, BttsResearchComparison } from "./types";

const rows = () => structuredClone(mockBttsResearch);
const reject = (change: (item: BttsResearchComparison) => void) => {
  const value = rows();
  change(value[0]);
  expect(() => decodeBttsResearch(value, "E1")).toThrow("outside the V1 contract");
};

afterEach(() => vi.unstubAllGlobals());

describe("BTTS research V1 contract", () => {
  it("uses an explicit E1-only competition registry", () => {
    expect(BTTS_COMPETITIONS).toEqual([{ code: "E1", name: "Championship" }]);
    expect(isBttsCompetition("E1")).toBe(true);
    for (const value of ["E0", "SP1", "I1", "D1", "ALL"]) expect(isBttsCompetition(value)).toBe(false);
  });

  it("accepts complete paired research values and preserves backend ordering", () => {
    const value = rows().reverse();
    expect(decodeBttsResearch(value, "E1")).toBe(value);
    expect(decodeBttsResearch([] as unknown, "E1")).toEqual([]);
  });

  it.each([
    ["probability", (item: BttsResearchComparison) => { item.yes.model_probability = 1.1; }],
    ["odds", (item: BttsResearchComparison) => { item.yes.decimal_odds = 1; }],
    ["decimal/American mismatch", (item: BttsResearchComparison) => { item.yes.decimal_odds = 5; }],
    ["difference", (item: BttsResearchComparison) => { item.yes.model_minus_market_difference += 0.01; }],
    ["EV", (item: BttsResearchComparison) => { item.no.expected_profit += 0.01; }],
    ["no-vig pair", (item: BttsResearchComparison) => { item.no.no_vig_market_probability -= 0.01; }],
    ["research designation", (item: BttsResearchComparison) => { (item as { research_only: boolean }).research_only = false; }],
    ["competition", (item: BttsResearchComparison) => { (item.fixture as { competition: string }).competition = "E0"; }],
    ["model provenance", (item: BttsResearchComparison) => { (item as { model_version: string }).model_version = "retuned"; }],
    ["timestamp order", (item: BttsResearchComparison) => { item.frozen_at_utc = item.observation_timestamp_utc; }],
    ["unexpected field", (item: BttsResearchComparison) => { Object.assign(item, { qualified: true }); }],
  ])("rejects invalid %s", (_name, change) => reject(change));

  it("requires historical states to clear current best-price flags", () => {
    reject((item) => { item.current_status = "STALE"; });
    const value = rows();
    value[0].current_status = "STALE";
    value[0].best_yes_price = false;
    expect(decodeBttsResearch(value, "E1")[0].current_status).toBe("STALE");
  });

  it("rejects duplicate best-side facts without reselecting a book", () => {
    const value = rows();
    value[1].best_yes_price = true;
    expect(() => decodeBttsResearch(value, "E1")).toThrow();
  });

  it("requests the selected supported competition and rejects unsupported input before fetch", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const client = createApiClient("live", "https://api.example.test/api/v1");
    await expect(client.bttsResearch("E1")).resolves.toEqual([]);
    expect(fetcher).toHaveBeenCalledWith("https://api.example.test/api/v1/research/btts?competition=E1",
      expect.objectContaining({ cache: "no-store" }));
    await expect(client.bttsResearch("E0" as BttsCompetition)).rejects.toMatchObject({ code: "UNSUPPORTED_COMPETITION" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
