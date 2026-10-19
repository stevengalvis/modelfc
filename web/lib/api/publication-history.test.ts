import { afterEach, expect, it, vi } from "vitest";
import { createApiClient } from "./client";
import { assertPublicationSelection, decodeTrackRecord, publicationSelection, emptyTrackRecord } from "./track-record";
import fixture from "../../../tests/fixtures/track_record.json";
import strategy from "../../../tests/fixtures/strategy_picks.json";
afterEach(()=>vi.unstubAllGlobals());
it("sends and verifies identical model-pick and strategy filters without caching", async()=>{
  const filters={competition:"E1",market:"TEAM_TOTAL",asOf:"2026-10-01T00:00:00Z"};
  const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({...fixture,selection:publicationSelection(filters)})))
    .mockResolvedValueOnce(new Response(JSON.stringify({...strategy,scope:"history",selection:publicationSelection({...filters,market:"BTTS"})})));
  vi.stubGlobal("fetch",fetcher);
  const client=createApiClient("live","https://api.example.test/api/v1");
  await client.trackRecord(undefined,filters);
  await client.strategyPicks({...filters,market:"BTTS",strategy:"best_value",scope:"history",offset:0});
  for(const [url,init] of fetcher.mock.calls){
    const query=new URL(url).searchParams;
    expect(query.get("as_of")).toBe(filters.asOf); expect(query.get("competition")).toBe("E1");
    expect(init.cache).toBe("no-store");
  }
});
it("rejects ignored cutoffs and wrong scope even for empty responses",async()=>{
  const filters={competition:"E0",market:"BTTS",asOf:"2026-01-01T00:00:00Z"};
  for(const selection of [undefined,publicationSelection({}),publicationSelection({...filters,competition:"E1"}),
    {...publicationSelection(filters),historical_store_membership:"VERIFIED"}]){
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(JSON.stringify({...emptyTrackRecord,selection}))));
    await expect(createApiClient("live","https://api.example.test/api/v1").trackRecord(undefined,filters)).rejects.toThrow();
  }
});
it("rejects publications after the echoed cutoff and accepts equivalent timezone instants",()=>{
 const report=decodeTrackRecord(fixture);
 const filters={asOf:"2026-09-20T02:00:29Z"};
 expect(()=>assertPublicationSelection({...report,selection:publicationSelection(filters)},filters)).toThrow();
 const valid={asOf:"2026-09-20T05:00:30+03:00"};
 expect(()=>assertPublicationSelection({...report,selection:publicationSelection({asOf:"2026-09-20T02:00:30Z"})},valid)).not.toThrow();
});
