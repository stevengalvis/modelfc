import { describe, expect, it, vi } from "vitest";
import teams from "../../../tests/fixtures/team_intelligence/teams.json";
import profiles from "../../../tests/fixtures/team_intelligence/profiles.json";
import { decodeTeams, decodeTeamProfile, decodeTeamInsights, isTeamId } from "./team-intelligence";
import { createApiClient } from "./client";

describe("Team Intelligence strict contract", () => {
  it("accepts backend generated fixtures and representative coverage states", () => {
    expect(decodeTeams(teams).teams).toHaveLength(4);
    expect(profiles.cardiff.summary.recency.trend_state).toBe("AVAILABLE");
    expect(profiles.birmingham.summary.recency.trend_state).toBe("INCOMPLETE_COVERAGE");
    expect(profiles.portsmouth.summary.recency.trend_state).toBe("INSUFFICIENT_SAMPLE");
    expect(profiles.millwall.summary.recency.last_five.state).toBe("INSUFFICIENT_SAMPLE");
    for (const [id, profile] of Object.entries(profiles)) expect(decodeTeamProfile(profile,id).summary.team.team_id).toBe(id);
    expect(decodeTeamInsights({metadata:teams.metadata,insights:teams.insights}).insights.length).toBeLessThanOrEqual(6);
  });
  it("rejects private fields recursively instead of forwarding them", () => {
    const mutations = [
      (v: any) => { v.bookmaker="private"; },
      (v: any) => { v.metadata.path="/private"; },
      (v: any) => { v.teams[0].primary.edge=.1; },
      (v: any) => { v.insights[0].recent.roi=1; },
    ];
    for (const mutate of mutations) { const data=structuredClone(teams); mutate(data); expect(()=>decodeTeams(data)).toThrow(/outside/); }
  });
  it("rejects nonfinite rates, invalid sums, duplicated IDs and fake zero trends", () => {
    for (const mutate of [
      (v:any)=>{v.teams[0].primary.won=Infinity;},
      (v:any)=>{v.teams[0].primary.won_sum+=1;},
      (v:any)=>{v.teams.push(v.teams[0]);},
      (v:any)=>{v.teams[0].recency.won_change=0;},
      (v:any)=>{v.metadata.data_cutoff="2026-02-30";},
    ]) {const v=structuredClone(teams);mutate(v);expect(()=>decodeTeams(v)).toThrow();}
  });
  it("rejects mismatched route identity and malformed thresholds/recency",()=>{
    expect(()=>decodeTeamProfile(profiles.portsmouth,"cardiff")).toThrow();
    const p=structuredClone(profiles.portsmouth);p.summary.thresholds[0].frequency=.123;
    expect(()=>decodeTeamProfile(p,"portsmouth")).toThrow();
  });
  it("supports explicit mock reads without falling back from live errors",async()=>{
    expect((await createApiClient("mock").teams()).teams.length).toBe(4);
    expect((await createApiClient("mock").teamProfile("portsmouth")).summary.team.team_id).toBe("portsmouth");
    await expect(createApiClient("mock").teamProfile("../../private")).rejects.toThrow(/not found/);
  });
  it("bounds slug syntax without duplicating backend registry membership", async () => {
    for (const id of ["", "Cardiff", "cardiff/", "../private", "club-2", "a--b", "a-b-c", "a".repeat(17), "a".repeat(16)+"-"+"b".repeat(16)]) expect(isTeamId(id)).toBe(false);
    expect(isTeamId("new-club")).toBe(true);
    const profile = structuredClone(profiles.cardiff);
    profile.summary.team.team_id = "new-club";
    profile.insights.forEach(i => { i.team.team_id = "new-club"; });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(profile), {status:200}));
    vi.stubGlobal("fetch", fetchMock);
    try {
      expect((await createApiClient("live", "https://example.test/api/v1").teamProfile("new-club")).summary.team.team_id).toBe("new-club");
      expect(fetchMock.mock.calls[0][0]).toBe("https://example.test/api/v1/teams/new-club");
    } finally { vi.unstubAllGlobals(); }
  });
  it("retains overview membership and route identity checks with syntax-only IDs", () => {
    const v=structuredClone(teams);
    v.insights[0].team.team_id="new-club";
    expect(()=>decodeTeams(v)).toThrow();
    expect(()=>decodeTeamProfile(profiles.cardiff,"new-club")).toThrow();
  });

});
