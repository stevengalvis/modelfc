import { describe, expect, it } from "vitest";
import teams from "../../../tests/fixtures/team_intelligence/teams.json";
import profiles from "../../../tests/fixtures/team_intelligence/profiles.json";
import { decodeTeams, decodeTeamProfile, decodeTeamInsights } from "./team-intelligence";
import { createApiClient } from "./client";

describe("Team Intelligence strict contract", () => {
  it("accepts backend generated fixtures and all canonical profiles", () => {
    expect(decodeTeams(teams).teams).toHaveLength(24);
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
    expect((await createApiClient("mock").teams()).teams.length).toBe(24);
    expect((await createApiClient("mock").teamProfile("portsmouth")).summary.team.team_id).toBe("portsmouth");
    await expect(createApiClient("mock").teamProfile("../../private")).rejects.toThrow(/not found/);
  });
});
