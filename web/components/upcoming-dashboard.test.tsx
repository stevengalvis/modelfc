import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api/client";
import { mockPredictions } from "../lib/api/mock";
import { emptyBttsPerformance } from "../lib/api/btts-performance";
import { loadUpcomingPicks, upcomingFixtures } from "../lib/upcoming";
import { UpcomingDashboard } from "./upcoming-dashboard";

import { decodeStrategyPage } from "../lib/api/track-record";
import strategyFixture from "../../tests/fixtures/strategy_picks.json";
import { RecommendationsDashboard } from "./recommendations-dashboard";

const page = decodeStrategyPage(strategyFixture);
const now = Date.parse("2026-10-12T12:00:00Z");
const corner = { ...mockPredictions[0], competition: "E1", home_team: "Home", away_team: "Away",
  kickoff_utc: "2026-10-13T12:00:00Z", created_at_utc: "2026-10-12T11:00:00Z", settlement_status: "UPCOMING" as const };
const btts = { ...corner, prediction_id: "btts", prediction_type: "BTTS" as const,
  btts_yes_probability: 0.57, btts_no_probability: 0.43, research_status: "RESEARCH_ONLY" as const };
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
function sources() {
  vi.spyOn(Date,"now").mockReturnValue(now);
  vi.spyOn(api,"predictions").mockResolvedValue([corner,btts]);
  vi.spyOn(api,"bttsPerformance").mockResolvedValue(emptyBttsPerformance);
  vi.spyOn(api,"recommendations").mockResolvedValue([]);
  vi.spyOn(api,"strategyPicks").mockResolvedValue({...page, publications: [], total: 0, next_offset: null});
}
describe("next Championship matches", () => {
  it("groups independent markets and repeated cohorts into one exact fixture", () => {
    const later = {...corner,prediction_id:"later",created_at_utc:"2026-10-12T11:30:00Z",expected_home_corners:99};
    const cards=upcomingFixtures([later,btts,corner],[],now);
    expect(cards).toHaveLength(1);
    expect(cards[0].corner?.values[0]).toBe(corner.expected_home_corners);
    expect(cards[0].btts?.values).toEqual([0.57,0.43]);
    expect(upcomingFixtures([{...corner,competition:"E0"},{...corner,kickoff_utc:"2026-10-12T11:00:00Z"}],[],now)).toEqual([]);
    expect(upcomingFixtures([corner,{...corner,kickoff_utc:"2026-10-13T13:00:00Z"}],[],now)).toHaveLength(2);
  });
  it("shows both market values, sample count, freeze age and history links", async () => {
    sources(); render(<UpcomingDashboard/>);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByText(/1 unique fixtures/)).toBeInTheDocument();
    expect(screen.getByText("57.0%")).toBeInTheDocument();
    expect(screen.getByText("43.0%")).toBeInTheDocument();
    expect(screen.getByText("Home corners")).toBeInTheDocument();
    expect(screen.getAllByText(/Frozen 1 hr ago/)).toHaveLength(2);
    expect(screen.getByRole("link",{name:"BTTS results"})).toHaveAttribute("href","/research/btts/performance");
  });
  it("does not hide a valid market when the other read fails; retry recovers", async () => {
    sources(); vi.mocked(api.predictions).mockResolvedValue([corner]);
    vi.mocked(api.bttsPerformance).mockRejectedValueOnce(new Error("private failure"));
    render(<UpcomingDashboard/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("BTTS");
    expect(screen.getByText("Home corners")).toBeInTheDocument();
    expect(screen.getByText("Blocked · read unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"Refresh"}));
    await waitFor(()=>expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText("Waiting · no recorded forecast")).toBeInTheDocument();
  });
  it("does not invent an upcoming schedule when only history or no data exists", async () => {
    sources(); vi.mocked(api.predictions).mockResolvedValue([{...corner,kickoff_utc:"2026-10-11T12:00:00Z"}]);
    render(<UpcomingDashboard/>);
    expect(await screen.findByText("No upcoming forecasts or published picks recorded")).toBeInTheDocument();
    expect(screen.getByText(/not the full fixture calendar/)).toBeInTheDocument();
  });
  it("does not treat a recommendation outage as missing fixture coverage", async () => {
    sources(); vi.mocked(api.predictions).mockResolvedValue([]);
    vi.mocked(api.recommendations).mockRejectedValue(new Error("down"));
    render(<UpcomingDashboard/>);
    expect(await screen.findByText("No upcoming forecasts or published picks recorded")).toBeInTheDocument();
    expect(screen.getByText(/0 unique fixtures/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Legacy opportunity status is unavailable");
  });
  it("distinguishes total source outage from empty successful reads", async () => {
    sources(); vi.mocked(api.predictions).mockRejectedValue(new Error("down"));
    vi.mocked(api.bttsPerformance).mockRejectedValue(new Error("down"));
    render(<UpcomingDashboard/>);
    expect(await screen.findByText("Upcoming coverage unavailable or incomplete")).toBeInTheDocument();
    expect(screen.getByText(/total coverage unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/0 unique fixtures/)).not.toBeInTheDocument();
    expect(screen.queryByText("No upcoming forecasts or published picks recorded")).not.toBeInTheDocument();
  });
});

it("uses the same immutable BTTS pick as Recommendations despite an empty legacy feed", async () => {
  sources();
  const pick = {...page.publications[0], offer: {...page.publications[0].offer, ...{home_team: "Home", away_team: "Away", kickoff_utc: corner.kickoff_utc}}};
  vi.mocked(api.strategyPicks).mockResolvedValue({...page, publications: [pick]});
  const view = render(<UpcomingDashboard/>);
  await screen.findByText("Both teams to score: Yes");
  const price = screen.getByText(/draftkings · \+120 · 67.0% model probability/).textContent;
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByText(/No legacy corner opportunity/)).toBeInTheDocument();
  expect(screen.queryByText(/BTTS is research only/)).not.toBeInTheDocument();
  expect(screen.getByText(/Stale quote/)).toBeInTheDocument();
  expect(screen.getByRole("link", {name: "Corner saved-price analysis · all fixtures"})).toHaveAttribute("href", "/recommendations?view=shortlist");
  expect(screen.getByRole("link", {name: "BTTS research prices · all fixtures"})).toHaveAttribute("href", "/research/btts");
  expect(screen.getByText(/may not contain this fixture or a newer quote/)).toBeInTheDocument();
  expect(screen.queryByRole("link", {name: /newer observed quotes/})).not.toBeInTheDocument();
  view.unmount(); render(<RecommendationsDashboard/>);
  expect(await screen.findByText(price!)).toBeInTheDocument();
});
it("includes publication-only fixtures and scopes unsupported research coverage", async () => {
  sources(); vi.mocked(api.predictions).mockResolvedValue([]);
  // The public corner contract supports these leagues independently of E1 research.
  const offer = {...corner, market_type: "TEAM_TOTAL", competition: "E0", team: "Home", direction: "OVER", line: 4.5, american_odds: 120, model_probability: .67, bookmaker: "draftkings", retrieved_at_utc: "2026-10-12T11:00:00Z"};
  vi.mocked(api.strategyPicks).mockResolvedValue({...page, publications: [{...page.publications[0], offer} as unknown as typeof page.publications[0]]});
  render(<UpcomingDashboard/>);
  expect(await screen.findByText("Home corners over 4.5")).toBeInTheDocument();
  expect(screen.getByText(/Unsupported league for research forecasts/)).toBeInTheDocument();
});
it("does not infer no picks when a strategy read fails", async () => {
  sources(); vi.mocked(api.strategyPicks).mockRejectedValue(new Error("offline"));
  render(<UpcomingDashboard/>);
  expect(await screen.findByText(/Published picks unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/No qualifying published pick returned/)).not.toBeInTheDocument();
});
it("scopes an empty publication read and missing forecast separately", async () => {
  sources(); vi.mocked(api.predictions).mockResolvedValue([corner]);
  render(<UpcomingDashboard/>);
  expect(await screen.findByText(/No qualifying published pick returned/)).toBeInTheDocument();
  expect(screen.getByText("Waiting · no recorded forecast")).toBeInTheDocument();
});
it("labels fixtures that start while the page remains open", async () => {
  sources(); vi.useFakeTimers(); vi.setSystemTime(now);
  render(<UpcomingDashboard/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  vi.setSystemTime(Date.parse(corner.kickoff_utc));
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(screen.getByText(/Started fixture · retained/)).toBeInTheDocument();
});
it("reads subsequent strategy pages and rejects non-advancing pagination", async () => {
  sources();
  const six = Array.from({length:6}, (_, i) => ({...page.publications[0], publication_id:`pick-${i}`}));
  vi.mocked(api.strategyPicks).mockImplementation(async q => ({...page, strategy:q.strategy, offset:q.offset, total:6, publications:six.slice(q.offset,q.offset+5), next_offset:q.offset ? null : 5}));
  expect(await loadUpcomingPicks(new AbortController().signal)).toHaveLength(6);
  expect(api.strategyPicks).toHaveBeenCalledWith(expect.objectContaining({strategy:"higher_probability", offset:5}), expect.any(AbortSignal));
  vi.mocked(api.strategyPicks).mockResolvedValue({...page, next_offset:0});
  await expect(loadUpcomingPicks(new AbortController().signal)).rejects.toThrow("Non-advancing");
});

it("reports unknown picks when kickoff shrinks the current cohort between pages", async () => {
  sources();
  const six = Array.from({length:6}, (_, i) => ({...page.publications[0], publication_id:`pick-${i}`, offer:{...page.publications[0].offer, home_team:i === 5 ? "Home" : `Team ${i}`, away_team:"Away", kickoff_utc:corner.kickoff_utc}}));
  vi.mocked(api.strategyPicks).mockImplementation(async q => ({...page, strategy:q.strategy, offset:q.offset,
    total:q.offset === 0 ? 6 : 5, publications:q.offset === 0 ? six.slice(0,5) : [], next_offset:q.offset === 0 ? 5 : null}));
  await expect(loadUpcomingPicks(new AbortController().signal)).rejects.toThrow(/Incomplete/);
  render(<UpcomingDashboard/>);
  expect(await screen.findByText(/Published picks unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/No qualifying published pick returned/)).not.toBeInTheDocument();
});

it("rejects duplicate offset rows even when totals stay constant", async () => {
  sources();
  const five = Array.from({length:5}, (_, i) => ({...page.publications[0], publication_id:`pick-${i}`}));
  vi.mocked(api.strategyPicks).mockImplementation(async q => ({...page, strategy:q.strategy, offset:q.offset,
    total:6, publications:q.offset === 0 ? five : [five[4]], next_offset:q.offset === 0 ? 5 : null}));
  await expect(loadUpcomingPicks(new AbortController().signal)).rejects.toThrow(/repeated publication/);
});
it("rejects a crossed kickoff even if new publication keeps the total unchanged", async () => {
  sources();
  const six = Array.from({length:6}, (_, i) => ({...page.publications[0], publication_id:`pick-${i}`}));
  six[0] = {...six[0], offer:{...six[0].offer, kickoff_utc:new Date(now+1000).toISOString()}};
  vi.mocked(api.strategyPicks).mockImplementation(async q => {
    if(q.offset) vi.mocked(Date.now).mockReturnValue(now+1000);
    return {...page, strategy:q.strategy, offset:q.offset, total:6, publications:six.slice(q.offset,q.offset+5), next_offset:q.offset === 0 ? 5 : null};
  });
  await expect(loadUpcomingPicks(new AbortController().signal)).rejects.toThrow(/kickoff crossed/);
});

it.each([true,false])("separates a publication outage from successful forecast reads (populated=%s)",async(populated)=>{
 sources(); vi.mocked(api.strategyPicks).mockRejectedValue(new Error("publication read failed"));
 if(!populated) vi.mocked(api.predictions).mockResolvedValue([]);
 render(<UpcomingDashboard/>);
 const alert=await screen.findByRole("alert");
 expect(alert).toHaveTextContent("Published pick coverage is incomplete.");
 expect(alert).not.toHaveTextContent("Forecast coverage is incomplete.");
 expect(alert).not.toHaveTextContent("Legacy opportunity status is unavailable.");
 if(populated) expect(screen.getByText("Home corners")).toBeInTheDocument();
 else expect(screen.getByRole("heading",{name:"Published pick coverage unavailable"})).toBeInTheDocument();
 expect(screen.queryByText(/No qualifying published pick returned/)).not.toBeInTheDocument();
});
