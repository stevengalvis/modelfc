import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { mockRecordedOffers } from "@/lib/api/mock-recorded-offers";
import type { Shortlist } from "@/lib/api/types";
import { RecommendationsDashboard } from "./recommendations-dashboard";
const data = (): Shortlist => ({ as_of_utc: mockRecordedOffers.as_of_utc, limit: 5, total: 1,
  offers: mockRecordedOffers.fixtures.flatMap(f => f.offers.map(o => ({ ...o, fixture_id: f.fixture_id }))) });
const fresh = () => { const v = data(); v.as_of_utc = "2026-10-10T13:04:59Z";
  Object.assign(v.offers[0], { observation_age_seconds: 299, status: "RECENT_QUALIFYING", current_recommendation: true }); return v; };
beforeEach(() => { vi.spyOn(api, "shortlist").mockResolvedValue(data());
  vi.spyOn(api, "alternatives").mockResolvedValue(structuredClone(mockRecordedOffers));
  vi.spyOn(api, "recommendations"); vi.spyOn(api, "recordedOffers"); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
describe("saved-price shortlist", () => {
  it("keeps old qualifying prices with timestamps without history or alternatives requests", async () => {
    render(<RecommendationsDashboard />); await screen.findByRole("article");
    expect(screen.getByText("Price needs rechecking")).toBeInTheDocument();
    expect(screen.getByText(/Age 20m/)).toBeInTheDocument();
    expect(api.shortlist).toHaveBeenCalledWith(5, expect.any(AbortSignal));
    expect(api.alternatives).not.toHaveBeenCalled(); expect(api.recordedOffers).not.toHaveBeenCalled(); expect(api.recommendations).not.toHaveBeenCalled();
  });
  it("loads more than five choices from one fixture on demand", async () => {
    const v=data(); v.total=6;
    const rows=Array.from({length:6},(_,i)=>({...v.offers[0],target_id:String(i).padStart(32,"0"),line:10.5+i}));
    v.offers=rows.slice(0,5);
    vi.mocked(api.shortlist).mockResolvedValueOnce(v).mockResolvedValue({...v,offers:rows,limit:10});
    render(<RecommendationsDashboard />); await screen.findAllByRole("article");
    expect(screen.getAllByRole("article")).toHaveLength(5);
    fireEvent.click(screen.getByText("Show more qualifying choices")); await act(async()=>{});
    expect(api.shortlist).toHaveBeenLastCalledWith(10,expect.any(AbortSignal));
    expect(screen.getAllByRole("article")).toHaveLength(6);
  });
  it("fetches and mounts alternatives only while open and aborts when closed", async()=>{
    render(<RecommendationsDashboard />); await screen.findByRole("article");
    expect(screen.queryByRole("region",{name:"Alternative bookmakers and lines"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Bookmakers and alternate lines"));
    await screen.findByRole("region",{name:"Alternative bookmakers and lines"});
    expect(api.alternatives).toHaveBeenCalledTimes(1);
    const signal=vi.mocked(api.alternatives).mock.calls[0][1];
    fireEvent.click(screen.getByText("Close alternatives")); expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole("region",{name:"Alternative bookmakers and lines"})).not.toBeInTheDocument();
  });
  it("preserves choices through stalled refreshes and retries every minute",async()=>{
    vi.useFakeTimers(); vi.mocked(api.shortlist).mockResolvedValueOnce(data()).mockImplementation(()=>new Promise(()=>{}));
    const {unmount}=render(<RecommendationsDashboard />); await act(async()=>{});
    await act(async()=>{await vi.advanceTimersByTimeAsync(60_000);});
    expect(screen.getByRole("article")).toBeInTheDocument();
    const stalled=vi.mocked(api.shortlist).mock.calls[1][1];
    await act(async()=>{await vi.advanceTimersByTimeAsync(60_000);});
    expect(stalled?.aborted).toBe(true); expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("article")).toBeInTheDocument(); expect(api.shortlist).toHaveBeenCalledTimes(3);
    unmount(); expect(vi.mocked(api.shortlist).mock.calls.at(-1)?.[1]?.aborted).toBe(true);
  });
  it("ages beyond five minutes without disappearing and excludes kickoff locally",async()=>{
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-10T13:04:59Z"));
    vi.mocked(api.shortlist).mockResolvedValueOnce(fresh()).mockImplementation(()=>new Promise(()=>{}));
    render(<RecommendationsDashboard />); await act(async()=>{});
    await act(async()=>{await vi.advanceTimersByTimeAsync(2000);});
    expect(screen.getByText("Price needs rechecking")).toBeInTheDocument(); expect(screen.getByRole("article")).toBeInTheDocument();
    vi.setSystemTime(new Date("2026-10-10T14:00:00Z")); fireEvent(window,new Event("focus")); await act(async()=>{});
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    vi.setSystemTime(new Date("2026-10-10T13:04:59Z")); fireEvent(window,new Event("focus")); await act(async()=>{});
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
  it("retains previous data on failure, marks recheck and recovers",async()=>{
    vi.mocked(api.shortlist).mockResolvedValueOnce(fresh()).mockRejectedValueOnce(new Error("offline")).mockResolvedValue(data());
    render(<RecommendationsDashboard />); await screen.findByRole("article");
    fireEvent(window,new Event("focus")); await screen.findByRole("alert");
    expect(screen.getByText("Price needs rechecking")).toBeInTheDocument(); expect(screen.getByRole("article")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry")); await act(async()=>{}); expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
