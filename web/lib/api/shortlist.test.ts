import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "./client";
import { decodeShortlist } from "./shortlist";
import { mockRecordedOffers } from "./mock-recorded-offers";
const data=()=>({as_of_utc:mockRecordedOffers.as_of_utc,limit:5,total:1,
  offers:mockRecordedOffers.fixtures.flatMap(f=>f.offers.map(o=>({...o,fixture_id:f.fixture_id})))});
afterEach(()=>vi.unstubAllGlobals());
describe("shortlist contract",()=>{
  it("accepts aged qualified prices",()=>{const v=data(); expect(decodeShortlist(v)).toBe(v);});
  it.each([{qualified:false},{expected_profit:0},{american_odds:-201},{no_vig_probability_edge:0.01},
    {status:"QUOTE_UNAVAILABLE"},{availability_checked_at_utc:null},{current_recommendation:true},
    {kickoff_utc:"2026-10-10T13:20:00Z"},{private_path:"secret"}])("rejects invalid row %j",change=>{
    const v=data(); Object.assign(v.offers[0],change); expect(()=>decodeShortlist(v)).toThrow();
  });
  it.each(["model_probability", "push_probability", "decisive_model_probability",
    "sportsbook_implied_probability", "no_vig_market_probability"])("rejects missing %s even with claimed positive EV",field=>{
    const v=data();Object.assign(v.offers[0],{[field]:null,expected_profit:99});
    expect(()=>decodeShortlist(v)).toThrow();
  });
  it("rejects duplicate targets/books and invalid counts",()=>{
    const v=data();v.offers.push({...v.offers[0],bookmaker:"other"});v.total=2;expect(()=>decodeShortlist(v)).toThrow();
    expect(()=>decodeShortlist({...data(),limit:101})).toThrow();expect(()=>decodeShortlist({...data(),total:3})).toThrow();
  });
  it("uses only shortlist and explicitly requested fixture GETs",async()=>{
    const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(data()),{status:200}))
      .mockResolvedValueOnce(new Response(JSON.stringify(mockRecordedOffers),{status:200}));
    vi.stubGlobal("fetch",fetcher);const client=createApiClient("live","https://api.example/api/v1"),signal=new AbortController().signal;
    await client.shortlist(5,signal);await client.alternatives("a".repeat(64),signal);
    expect(fetcher).toHaveBeenNthCalledWith(1,"https://api.example/api/v1/recommendations?view=shortlist&limit=5&ranking=best_value",expect.objectContaining({signal,cache:"no-store"}));
    expect(fetcher).toHaveBeenNthCalledWith(2,"https://api.example/api/v1/recommendations?view=alternatives&fixture_id="+"a".repeat(64),expect.objectContaining({signal,cache:"no-store"}));
  });  function probabilityRow(win: number, push = 0) {
    const v = data(); const row = v.offers[0];
    row.model_probability = win; row.push_probability = push;
    row.decisive_model_probability = win / (1 - push);
    row.no_vig_probability_edge = row.decisive_model_probability - row.no_vig_market_probability!;
    row.expected_profit = win * (row.decimal_odds - 1) - (1 - push - win);
    if (push) row.line = 6;
    return v;
  }
  it("uses actual win probability, includes 65% and rejects unfiltered older API results", () => {
    expect(decodeShortlist(probabilityRow(.65), "higher_probability").total).toBe(1);
    const conditional = probabilityRow(.64, .1);
    expect(conditional.offers[0].decisive_model_probability).toBeGreaterThan(.65);
    expect(() => decodeShortlist(conditional, "higher_probability")).toThrow();
    expect(decodeShortlist(conditional, "best_value").total).toBe(1);
  });
  it("rejects probability ranking inversions", () => {
    const v = probabilityRow(.7); v.total = 2;
    v.offers.push({ ...probabilityRow(.8).offers[0], target_id: "c".repeat(32), line: 7.5 });
    expect(() => decodeShortlist(v, "higher_probability")).toThrow();
    v.offers.reverse(); expect(decodeShortlist(v, "higher_probability")).toBe(v);
  });
  it("sends the selected ranking and filters mock data before pagination", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(probabilityRow(.7)), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const client = createApiClient("live", "https://api.example/api/v1");
    await client.shortlist(5, undefined, "higher_probability");
    expect(fetcher).toHaveBeenCalledWith("https://api.example/api/v1/recommendations?view=shortlist&limit=5&ranking=higher_probability", expect.anything());
    const mock = createApiClient("mock");
    expect((await mock.shortlist(1, undefined, "higher_probability")).total).toBe(0);
    expect((await mock.shortlist(1, undefined, "best_value")).total).toBe(1);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

});
