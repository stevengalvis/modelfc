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
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("saved-price shortlist", () => {
  it("keeps old qualifying prices with timestamps without history or alternatives requests", async () => {
    render(<RecommendationsDashboard />); await screen.findByRole("article");
    expect(screen.getByText("Price needs rechecking")).toBeInTheDocument();
    expect(screen.getByText(/Age 20m/)).toBeInTheDocument();
    expect(api.shortlist).toHaveBeenCalledWith(5, expect.any(AbortSignal), "higher_probability");
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
    expect(api.shortlist).toHaveBeenLastCalledWith(10,expect.any(AbortSignal), "higher_probability");
    expect(screen.getAllByRole("article")).toHaveLength(6);
  });
  function observe() {
    let notify: IntersectionObserverCallback;
    const disconnect = vi.fn();
    vi.stubGlobal("IntersectionObserver", vi.fn(function(callback: IntersectionObserverCallback) {
      notify = callback;
      return { observe: vi.fn(), disconnect };
    }));
    return { enter: () => act(() => notify([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)), disconnect };
  }
  function page(total = 12, limit = 5): Shortlist {
    const v = data();
    return { ...v, total, limit, offers: Array.from({ length: Math.min(total, limit) }, (_, i) =>
      ({ ...v.offers[0], target_id: String(i).padStart(32, "0"), line: 10.5 + i })) };
  }
  it("waits for scrolling, loads bounded batches and guards repeated observer events in flight", async () => {
    const observer = observe();
    let finish!: (value: Shortlist) => void;
    vi.mocked(api.shortlist).mockResolvedValueOnce(page()).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const { unmount } = render(<RecommendationsDashboard />); await screen.findAllByRole("article");
    observer.enter(); expect(api.shortlist).toHaveBeenCalledTimes(1);
    fireEvent.scroll(window); observer.enter(); observer.enter();
    expect(api.shortlist).toHaveBeenCalledTimes(2);
    expect(api.shortlist).toHaveBeenLastCalledWith(10, expect.any(AbortSignal), "higher_probability");
    expect(screen.getByText("Loading more qualifying choices…")).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(5);
    await act(async () => finish(page(12, 10)));
    expect(screen.getAllByRole("article")).toHaveLength(10);
    observer.enter(); expect(api.shortlist).toHaveBeenCalledTimes(2);
    expect(api.alternatives).not.toHaveBeenCalled();
    unmount(); expect(observer.disconnect).toHaveBeenCalled();
  });
  it("stops loading after exhaustion without duplicating the prefix", async () => {
    const observer = observe();
    vi.mocked(api.shortlist).mockResolvedValueOnce(page(6)).mockResolvedValueOnce(page(6, 10));
    render(<RecommendationsDashboard />); await screen.findAllByRole("article");
    fireEvent.scroll(window); observer.enter(); await act(async () => {});
    expect(screen.getAllByRole("article")).toHaveLength(6);
    expect(screen.getByText("All qualifying choices loaded.")).toBeInTheDocument();
    fireEvent.scroll(window); observer.enter();
    expect(api.shortlist).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Show more qualifying choices")).not.toBeInTheDocument();
  });
  it("retains the prefix on errors and requires explicit retry of the same batch", async () => {
    vi.useFakeTimers();
    const observer = observe();
    vi.mocked(api.shortlist).mockResolvedValueOnce(page(6)).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(page(6, 10));
    render(<RecommendationsDashboard />); await act(async () => {});
    fireEvent.scroll(window); observer.enter(); await act(async () => {});
    expect(screen.getAllByRole("article")).toHaveLength(5);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    observer.enter(); fireEvent.scroll(window); fireEvent.focus(window);
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(api.shortlist).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByText("Retry loading qualifying choices")); await act(async () => {});
    expect(api.shortlist).toHaveBeenLastCalledWith(10, expect.any(AbortSignal), "higher_probability");
    expect(screen.getAllByRole("article")).toHaveLength(6);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("aborts a stalled incremental read and waits for a manual retry", async () => {
    vi.useFakeTimers();
    const observer = observe();
    vi.mocked(api.shortlist).mockResolvedValueOnce(page(6)).mockImplementationOnce(() => new Promise(() => {})).mockResolvedValueOnce(page(6, 10));
    render(<RecommendationsDashboard />); await act(async () => {});
    fireEvent.scroll(window); observer.enter(); await act(async () => {});
    const signal = vi.mocked(api.shortlist).mock.calls[1][1];
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(signal?.aborted).toBe(true);
    expect(api.shortlist).toHaveBeenCalledTimes(2);
    expect(screen.getAllByRole("article")).toHaveLength(5);
    fireEvent.click(screen.getByText("Retry loading qualifying choices")); await act(async () => {});
    expect(screen.getAllByRole("article")).toHaveLength(6);
  });
  it("stops at the bounded 100-choice limit", async () => {
    vi.mocked(api.shortlist).mockImplementation(async limit => page(105, limit));
    render(<RecommendationsDashboard />); await screen.findAllByRole("article");
    for (let limit = 10; limit <= 100; limit += 5) {
      fireEvent.click(screen.getByText("Show more qualifying choices")); await act(async () => {});
    }
    expect(screen.getAllByRole("article")).toHaveLength(100);
    expect(screen.getByText(/Showing the top 100 qualifying choices/)).toBeInTheDocument();
    expect(screen.queryByText("Show more qualifying choices")).not.toBeInTheDocument();
    expect(api.shortlist).toHaveBeenCalledTimes(20);
  });
  it("defaults to higher probability and resets pagination while aborting the previous mode", async () => {
    let finish!: (value: Shortlist) => void;
    vi.mocked(api.shortlist).mockResolvedValueOnce(page(12)).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValueOnce({ ...data(), offers: [{ ...data().offers[0], line: 3.5 }] }).mockResolvedValueOnce(page(12));
    render(<RecommendationsDashboard />); await screen.findAllByRole("article");
    expect(screen.getByRole("combobox", { name: "Ranking" })).toHaveValue("higher_probability");
    fireEvent.click(screen.getByText("Show more qualifying choices")); await act(async () => {});
    const oldSignal = vi.mocked(api.shortlist).mock.calls[1][1];
    screen.getByRole("combobox").focus();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "best_value" } }); await act(async () => {});
    expect(screen.getByRole("combobox")).toHaveFocus();
    expect(oldSignal?.aborted).toBe(true);
    expect(api.shortlist).toHaveBeenLastCalledWith(5, expect.any(AbortSignal), "best_value");
    expect(screen.getAllByRole("article")).toHaveLength(1);
    await act(async () => finish(page(12, 10)));
    expect(screen.getAllByRole("article")).toHaveLength(1);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "higher_probability" } }); await act(async () => {});
    expect(api.shortlist).toHaveBeenLastCalledWith(5, expect.any(AbortSignal), "higher_probability");
    expect(screen.getAllByRole("article")).toHaveLength(5);
  });
  it("shows an honest empty higher-probability result and explains estimates", async () => {
    vi.mocked(api.shortlist).mockResolvedValue({ ...data(), total: 0, offers: [] });
    render(<RecommendationsDashboard />);
    expect(await screen.findByText(/No upcoming choices meet both the value checks and at least 65%/)).toBeInTheDocument();
    expect(screen.getByText(/Model estimates are not proven hit rates/)).toBeInTheDocument();
    expect(screen.queryByText("Show more qualifying choices")).not.toBeInTheDocument();
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
