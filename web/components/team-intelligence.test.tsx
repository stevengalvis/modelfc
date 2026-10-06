import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import teams from "../../tests/fixtures/team_intelligence/teams.json";
import profiles from "../../tests/fixtures/team_intelligence/profiles.json";
import { decodeTeams, decodeTeamProfile } from "@/lib/api/team-intelligence";
import { TeamsDashboard, TeamProfileDashboard } from "./team-intelligence";
const reads=vi.hoisted(()=>({teams:vi.fn(),teamProfile:vi.fn()}));
vi.mock("@/lib/api/client",()=>({api:reads}));
afterEach(cleanup);
beforeEach(()=>{vi.clearAllMocks();reads.teams.mockResolvedValue(decodeTeams(teams));reads.teamProfile.mockResolvedValue(decodeTeamProfile(profiles.portsmouth,"portsmouth"));});
describe("Team Intelligence pages",()=>{
 it("shows cutoff, canonical links, samples and honest early-season trend",async()=>{
  const {container}=render(<TeamsDashboard/>);
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
  await screen.findByText(/Data through/);
  expect(screen.getAllByText("Trend available after 10 matches")).toHaveLength(4);
  expect(screen.getAllByRole("link",{name:"Portsmouth"})[0]).toHaveAttribute("href","/teams/portsmouth");
  expect(container.querySelector(".ti-mobile")).toBeInTheDocument();
  expect(screen.getByRole("link", {name:"Compare two teams →"})).toHaveAttribute("href", "/teams/compare");
  fireEvent.click(screen.getByRole("button",{name:"Won / match"}));
  expect(container.querySelector('th[aria-sort="descending"]')).toHaveTextContent("Won / match");
 });
 it("renders profile venues, thresholds and at most ten match summaries",async()=>{
  const {container}=render(<TeamProfileDashboard teamId="portsmouth"/>);
  await screen.findByRole("heading",{level:1,name:"Portsmouth"});
  expect(screen.getByRole("heading",{name:"Home and away"})).toBeInTheDocument();
  expect(screen.getByRole("heading",{name:"Match corner thresholds"})).toBeInTheDocument();
  expect(container.querySelectorAll('.ti-recent article')).toHaveLength(8);
  expect(screen.getByRole("link", {name:"Compare with another team →"})).toHaveAttribute("href", "/teams/compare?team=portsmouth");
  expect(screen.getByText(/Lowest rate ranks first/)).toBeInTheDocument();
  expect(screen.getByText("Trend available after 10 matches")).toBeInTheDocument();
 });
 it("shows an empty season separately from a request failure",async()=>{
  reads.teams.mockResolvedValue({...decodeTeams(teams),teams:[],insights:[]});
  render(<TeamsDashboard/>);await screen.findByText(/No completed current-season/);
 });
 it("keeps errors visible and retryable without mock substitution",async()=>{
  reads.teams.mockRejectedValueOnce(new Error("Team history is temporarily unavailable."));
  render(<TeamsDashboard/>);await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button",{name:"Retry"}));await screen.findByText(/Data through/);
  expect(reads.teams).toHaveBeenCalledTimes(2);
 });
 it("shows unknown-team errors",async()=>{
  reads.teamProfile.mockRejectedValue(new Error("Team was not found."));
  render(<TeamProfileDashboard teamId="unknown"/>);await screen.findByText("Team was not found.");
 });
});
