import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeRecordedOffers } from "./recorded-offers";
import { mockRecordedOffers } from "./mock-recorded-offers";
import { createApiClient } from "./client";
const data = () => structuredClone(mockRecordedOffers);
afterEach(() => vi.unstubAllGlobals());
describe("recorded offer contract", () => {
  it("accepts stale observations and empty snapshots without changing values", () => {
    const v=data(); expect(decodeRecordedOffers(v)).toBe(v);
    expect(decodeRecordedOffers({...v,fixtures:[]})).toEqual({...v,fixtures:[]});
  });
  it.each([{status:'RECENT_QUALIFYING',current_recommendation:true}, {observation_age_seconds:2},
    {market_type:'BTTS'}, {market_type:'MATCH_TOTAL'}, {retrieved_at_utc:'2026-10-10T15:00:00Z'},
    {expected_profit:99}, {current_recommendation:true}, {private_path:'/secret'}, {opportunity_id:'../x'}])("rejects contradictory offer %j", change => {
    const v=data(); Object.assign(v.fixtures[0].offers[0],change); expect(()=>decodeRecordedOffers(v)).toThrow();
  });
  it("rejects duplicate books and fixtures, malformed cohort identity and false research eligibility", () => {
    let v=data(); v.fixtures.push(v.fixtures[0]); expect(()=>decodeRecordedOffers(v)).toThrow();
    v=data(); v.fixtures[0].offers.push(v.fixtures[0].offers[0]); expect(()=>decodeRecordedOffers(v)).toThrow();
    v=data(); v.fixtures[0].forecasts[0].research_only=true; expect(()=>decodeRecordedOffers(v)).toThrow();
    v=data(); v.fixtures[0].offers[0].prediction_id='c'.repeat(32); expect(()=>decodeRecordedOffers(v)).toThrow();
  });
  it("accepts the forecast-only research identity without inventing offers", () => {
    const v=data(); v.fixtures[0].offers=[]; v.fixtures[0].selected_prediction_id=null;
    Object.assign(v.fixtures[0].forecasts[0], {prediction_id:'f'.repeat(64), research_only:true});
    expect(decodeRecordedOffers(v)).toEqual(v);
  });
  it("uses a read-only no-store query on the existing endpoint", async () => {
    const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(data()),{status:200})); vi.stubGlobal('fetch',fetcher);
    const signal=new AbortController().signal;
    expect(await createApiClient('live','https://api.example/api/v1').recordedOffers(signal)).toEqual(data());
    expect(fetcher).toHaveBeenCalledWith('https://api.example/api/v1/recommendations?view=recorded',expect.objectContaining({signal,cache:'no-store'}));
  });
  it("does not silently use a legacy empty recommendation array", async () => {
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('[]',{status:200})));
    await expect(createApiClient('live','https://api.example/api/v1').recordedOffers()).rejects.toThrow('outside the V1 contract');
  });
});
