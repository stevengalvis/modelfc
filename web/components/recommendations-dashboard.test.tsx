import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { mockRecordedOffers } from "@/lib/api/mock-recorded-offers";
import { RecommendationsDashboard } from "./recommendations-dashboard";

const data = () => structuredClone(mockRecordedOffers);
const fresh = () => { const v = data(); v.as_of_utc = "2026-10-10T13:04:59Z";
  Object.assign(v.fixtures[0].offers[0], { observation_age_seconds: 299, status: "RECENT_QUALIFYING", current_recommendation: true }); return v; };
beforeEach(() => { vi.spyOn(api, "recommendations").mockResolvedValue([]); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("recorded Recommendations", () => {
  it("distinguishes a pending current feed from a successful empty feed with saved cards", async () => {
    let resolve!: (items: []) => void;
    vi.mocked(api.recommendations).mockImplementation(() => new Promise(done => { resolve = done; }));
    vi.spyOn(api, "recordedOffers").mockResolvedValue(data());
    render(<RecommendationsDashboard />);
    await screen.findByRole("article");
    expect(screen.getByText("Loading current recommendations…")).toBeInTheDocument();
    expect(screen.queryByText("No current recommendations")).not.toBeInTheDocument();
    await act(async () => { resolve([]); });
    expect(screen.getByText("No current recommendations")).toBeInTheDocument();
    expect(screen.queryByText("Loading current recommendations…")).not.toBeInTheDocument();
  });
  it("ages through system sleep on focus even if monotonic time pauses and refresh hangs", async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-10T13:04:59Z"));
    vi.spyOn(performance, "now").mockReturnValue(100);
    vi.spyOn(api, "recordedOffers").mockResolvedValueOnce(fresh()).mockImplementation(() => new Promise(() => {}));
    render(<RecommendationsDashboard />); await act(async () => {});
    expect(screen.getByText("Recently checked · qualifying")).toBeInTheDocument();
    vi.setSystemTime(new Date("2026-10-10T15:00:00Z"));
    fireEvent(window, new Event("focus")); await act(async () => {});
    expect(screen.queryByText("Recently checked · qualifying")).not.toBeInTheDocument();
    expect(screen.getByText("Kicked-off / historical fixtures (1)")).toBeInTheDocument();
    // A backwards wall-clock adjustment cannot make retained quotes current again.
    vi.setSystemTime(new Date("2026-10-10T13:04:59Z"));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText("Kicked-off / historical fixtures (1)")).toBeInTheDocument();
  });
  it("preserves current offers from earlier cohorts independently of recorded selection", async () => {
    const { mockRecommendations } = await import("@/lib/api/mock-recommendations");
    const older = structuredClone(mockRecommendations[0]);
    older.prediction_id = "a".repeat(64);
    older.observation_age_seconds = 0;
    vi.mocked(api.recommendations).mockResolvedValue([older]);
    vi.spyOn(api, "recordedOffers").mockResolvedValue(data());
    render(<RecommendationsDashboard />);
    const feed = screen.getByRole("region", {name: "Current recommendations across all forecast cohorts"});
    await within(feed).findByText(`Frozen forecast ${older.prediction_id}`);
    expect(within(feed).getByText("Recently checked · qualifying")).toBeInTheDocument();
    expect(within(feed).getByRole("link", {name: "Recorded forecast evidence"})).toHaveAttribute("href", `/predictions#prediction-${older.prediction_id}`);
    expect(await screen.findByRole("article")).toBeInTheDocument();
  });
  it("keeps the current feed visible when recorded history fails and clears it on current-read failure", async () => {
    const { mockRecommendations } = await import("@/lib/api/mock-recommendations");
    vi.mocked(api.recommendations).mockResolvedValue(structuredClone(mockRecommendations));
    vi.spyOn(api, "recordedOffers").mockRejectedValue(new Error("unavailable"));
    render(<RecommendationsDashboard />);
    const feed = screen.getByRole("region", {name: "Current recommendations across all forecast cohorts"});
    await within(feed).findByText("Recently checked · qualifying");
    vi.mocked(api.recommendations).mockRejectedValue(new Error("private"));
    fireEvent(window, new Event("focus"));
    await within(feed).findByRole("alert");
    expect(within(feed).queryByText("Recently checked · qualifying")).not.toBeInTheDocument();
  });
  it("expires current-feed offers locally without hiding their recorded cards", async () => {
    vi.useFakeTimers();
    const { mockRecommendations } = await import("@/lib/api/mock-recommendations");
    vi.mocked(api.recommendations).mockResolvedValue([{...mockRecommendations[0], observation_age_seconds:299}]);
    vi.spyOn(api, "recordedOffers").mockResolvedValue(data());
    render(<RecommendationsDashboard />); await act(async () => {});
    const feed = screen.getByRole("region", {name: "Current recommendations across all forecast cohorts"});
    expect(within(feed).getByText("Recently checked · qualifying")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(within(feed).queryByText("Recently checked · qualifying")).not.toBeInTheDocument();
    expect(screen.getByRole("article")).toBeInTheDocument();
  });
  it("keeps stale prices/value visible with timestamp and evidence links", async () => {
    vi.spyOn(api, "recordedOffers").mockResolvedValue(data());
    render(<RecommendationsDashboard />);
    const card = await screen.findByRole("article");
    expect(within(card).getByRole("heading", { name: "Demo West Ham vs Demo QPR" })).toBeInTheDocument();
    expect(within(card).getByText("Price needs rechecking")).toBeInTheDocument();
    expect(within(card).getByText("FanDuel · −105")).toBeInTheDocument();
    expect(within(card).getByText(/20m 0s/)).toBeInTheDocument();
    expect(within(card).getByText("Expected value at quote")).toBeInTheDocument();
    expect(within(card).getByRole("link", {name: "Recorded forecast evidence"})).toHaveAttribute("href", "/predictions#prediction-"+data().fixtures[0].selected_prediction_id);
    expect(screen.getByText(/does not fetch sportsbook odds/)).toBeInTheDocument();
  });
  it("downgrades a recently checked quote locally after five minutes without hiding it", async () => {
    vi.useFakeTimers(); vi.spyOn(api, "recordedOffers").mockResolvedValue(fresh());
    render(<RecommendationsDashboard />); await act(async () => {});
    expect(screen.getByText("Recently checked · qualifying")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText("Price needs rechecking")).toBeInTheDocument();
    expect(screen.getByRole("article")).toBeInTheDocument();
  });
  it("retains cards during pending refresh and failure without advertising current eligibility", async () => {
    vi.useFakeTimers(); let fail!: (e: Error) => void;
    const get = vi.spyOn(api, "recordedOffers").mockResolvedValueOnce(fresh()).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
    render(<RecommendationsDashboard />); await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(get).toHaveBeenCalledTimes(2); expect(screen.getByRole("article")).toBeInTheDocument();
    await act(async () => { fail(new Error("private failure")); });
    expect(screen.getByRole("alert")).toHaveTextContent("Showing the previous read");
    expect(screen.queryByText(/private failure/)).not.toBeInTheDocument();
    expect(screen.getByText("Price needs rechecking")).toBeInTheDocument();
  });
  it("moves a fixture to historical at kickoff without discarding the quote", async () => {
    vi.useFakeTimers(); const v = data(); v.as_of_utc = "2026-10-10T13:59:59Z";
    v.fixtures[0].offers[0].observation_age_seconds = 3599;
    vi.spyOn(api, "recordedOffers").mockResolvedValue(v);
    render(<RecommendationsDashboard />); await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText("Kicked-off / historical fixtures (1)")).toBeInTheDocument();
    expect(screen.queryByText("Historical quote · kickoff passed")).not.toBeInTheDocument();
    const history = screen.getByText("Kicked-off / historical fixtures (1)").closest("details")!;
    await act(async () => { history.open = true; fireEvent(history, new Event("toggle")); });
    expect(screen.getByText("Historical quote · kickoff passed")).toBeInTheDocument();
    await act(async () => { history.open = false; fireEvent(history, new Event("toggle")); });
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByRole("link", {name: "Open the full forecast ledger"})).toHaveAttribute("href", "/predictions");
    expect(screen.getByText("No upcoming recorded fixtures")).toBeInTheDocument();
  });
  it("groups multiple books and lines in one fixture with expanded detail", async () => {
    const v = data(); const base = v.fixtures[0].offers[0];
    v.fixtures[0].offers = [base, {...base, bookmaker: "DraftKings"}, ...[1,2,3].map(n => ({...base, line: base.line+n, target_id: String(n).repeat(32)}))];
    vi.spyOn(api, "recordedOffers").mockResolvedValue(v);
    render(<RecommendationsDashboard />);
    await screen.findByRole("article");
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByText("All 5 recorded offers · bookmakers and lines")).toBeInTheDocument();
    expect(screen.getByText("DraftKings · −105")).toBeInTheDocument();
  });
  it.each([['QUOTE_UNAVAILABLE', 'Quote unavailable at last check'], ['NO_QUALIFYING_EDGE', 'No qualifying edge'], ['VALUE_UNVERIFIED', 'Value comparison incomplete']])("labels %s separately", async (status, label) => {
    const v = fresh(); Object.assign(v.fixtures[0].offers[0], {status, current_recommendation:false});
    vi.spyOn(api, "recordedOffers").mockResolvedValue(v);
    render(<RecommendationsDashboard />); await screen.findByText(label);
  });
  it("shows forecast-only fixtures and labels BTTS as research", async () => {
    const v = data(); v.fixtures[0].offers = []; v.fixtures[0].selected_prediction_id = null;
    v.fixtures[0].forecasts.push({...v.fixtures[0].forecasts[0], prediction_id:'f'.repeat(64), prediction_type:'BTTS', research_only:true, btts_yes_probability:0.61, expected_home_corners:null, expected_away_corners:null});
    vi.spyOn(api, "recordedOffers").mockResolvedValue(v);
    render(<RecommendationsDashboard />);
    await screen.findByText(/No recorded corner odds/);
    expect(screen.getByText("BTTS research · not a recommendation")).toBeInTheDocument();
    expect(screen.getByText("BTTS Yes probability: 61.0%")).toBeInTheDocument();
    expect(screen.queryByText("Recently checked · qualifying")).not.toBeInTheDocument();
  });
  it("refreshes on focus, cleans up timers and handles initial errors", async () => {
    const get = vi.spyOn(api, "recordedOffers").mockRejectedValueOnce(new Error('private')).mockResolvedValue(data());
    const view = render(<RecommendationsDashboard />);
    await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', {name:'Retry'}));
    await screen.findByRole('article'); fireEvent(window, new Event('focus')); await act(async()=>{});
    expect(get).toHaveBeenCalledTimes(3); view.unmount(); fireEvent(window, new Event('focus'));
    expect(get).toHaveBeenCalledTimes(3);
  });
});
