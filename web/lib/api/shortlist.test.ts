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
    expect(fetcher).toHaveBeenNthCalledWith(1,"https://api.example/api/v1/recommendations?view=shortlist&limit=5",expect.objectContaining({signal,cache:"no-store"}));
    expect(fetcher).toHaveBeenNthCalledWith(2,"https://api.example/api/v1/recommendations?view=alternatives&fixture_id="+"a".repeat(64),expect.objectContaining({signal,cache:"no-store"}));
  });
});
