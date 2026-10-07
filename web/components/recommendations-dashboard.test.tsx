import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import { mockRecommendations } from "@/lib/api/mock-recommendations";
import type { Recommendation } from "@/lib/api/types";
import RecommendationsPage from "@/app/recommendations/page";
import { RecommendationsDashboard } from "./recommendations-dashboard";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("Recommendations workspace", () => {
  it("refreshes the API after a minute and removes backend-expired recommendations", async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(api, "recommendations").mockResolvedValueOnce(mockRecommendations).mockResolvedValueOnce([]);
    render(<RecommendationsDashboard />);
    await act(async () => {});
    expect(screen.getByRole("article")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[0][0]?.aborted).toBe(true);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByText("No current recommendations")).toBeInTheDocument();
  });
  it("refreshes an empty response to discover backend-returned recommendations", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "recommendations").mockResolvedValueOnce([]).mockResolvedValueOnce(mockRecommendations);
    render(<RecommendationsDashboard />);
    await act(async () => {});
    expect(screen.getByText("No current recommendations")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByRole("article")).toBeInTheDocument();
  });
  it("hides the previous offers while a refresh is pending and on refresh failure", async () => {
    vi.useFakeTimers();
    let fail!: (error: Error) => void;
    vi.spyOn(api, "recommendations").mockResolvedValueOnce(mockRecommendations)
      .mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
    render(<RecommendationsDashboard />);
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    await act(async () => { fail(new Error("private backend failure")); });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
  it("refreshes on window focus and removes the listener on unmount", async () => {
    const get = vi.spyOn(api, "recommendations").mockResolvedValue(mockRecommendations);
    const view = render(<RecommendationsDashboard />);
    await screen.findByRole("article");
    fireEvent(window, new Event("focus"));
    await act(async () => {});
    expect(get).toHaveBeenCalledTimes(2);
    view.unmount();
    fireEvent(window, new Event("focus"));
    expect(get).toHaveBeenCalledTimes(2);
  });
  it("clears the scheduled refresh on unmount", async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(api, "recommendations").mockResolvedValue(mockRecommendations);
    const view = render(<RecommendationsDashboard />);
    await act(async () => {});
    view.unmount();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(get).toHaveBeenCalledTimes(1);
  });
  it("renders the page, active navigation and all backend card facts", async () => {
    vi.spyOn(api, "recommendations").mockResolvedValue(structuredClone(mockRecommendations));
    render(<RecommendationsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Recommendations" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Recommendations" })).toHaveAttribute("aria-current", "page");
    const card = await screen.findByRole("article");
    expect(within(card).getByText("Demo West Ham vs Demo QPR · E1")).toBeInTheDocument();
    expect(within(card).getByRole("heading", { name: "Demo West Ham UNDER 5.5 CORNERS" })).toBeInTheDocument();
    for (const value of ["-105", "64.2%", "53.1%", "+11.1 pp", "+$0.25 / $1"])
      expect(within(card).getByText(value)).toBeInTheDocument();
    expect(card).toHaveTextContent("FanDuel");
    expect(card).toHaveTextContent("Kickoff 10 Oct 2026, 14:00 UTC");
    expect(card).toHaveTextContent("Retrieved 10 Oct 2026, 13:00 UTC");
    expect(card).toHaveTextContent("Availability checked 10 Oct 2026, 13:00 UTC");
    expect(card).toHaveTextContent("Quote age at read: 60s");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
  it("formats positive prices and signed negative values without hiding signs", async () => {
    vi.spyOn(api, "recommendations").mockResolvedValue([{ ...mockRecommendations[0],
      american_odds: 125, no_vig_probability_edge: -0.011, expected_profit: -0.25 }]);
    render(<RecommendationsDashboard />);
    expect(await screen.findByText("+125")).toBeInTheDocument();
    expect(screen.getByText("-1.1 pp")).toBeInTheDocument();
    expect(screen.getByText("-$0.25 / $1")).toBeInTheDocument();
  });
  it("preserves returned order without price comparison or client sorting", async () => {
    vi.spyOn(api, "recommendations").mockResolvedValue([mockRecommendations[0],
      { ...mockRecommendations[0], target_id: "second", team: "Demo QPR", team_side: "AWAY", expected_profit: 10 }]);
    render(<RecommendationsDashboard />);
    await screen.findAllByRole("article");
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent))
      .toEqual(["Demo West Ham UNDER 5.5 CORNERS", "Demo QPR UNDER 5.5 CORNERS"]);
  });
  it("renders the intentional empty state", async () => {
    vi.spyOn(api, "recommendations").mockResolvedValue([]);
    render(<RecommendationsDashboard />);
    expect(await screen.findByRole("heading", { name: "No current recommendations" })).toBeInTheDocument();
    expect(screen.getByText("Zeno will surface qualified team-corner opportunities here when fresh sportsbook prices are available.")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
  it("has accessible loading until the request completes", async () => {
    let finish!: (items: Recommendation[]) => void;
    vi.spyOn(api, "recommendations").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<RecommendationsDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading recommendations");
    await act(async () => { finish([]); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
  it.each([new Error("SECRET /private/ledger stack trace"),
    new ModelFCApiError("SECRET /private/ledger", "LEDGER_INTEGRITY_FAILURE", false, 503)])
  ("sanitizes errors and retries with a new request", async (error) => {
    const get = vi.spyOn(api, "recommendations").mockRejectedValueOnce(error).mockResolvedValueOnce([]);
    render(<RecommendationsDashboard />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load current recommendations");
    expect(alert).not.toHaveTextContent("SECRET");
    expect(alert).not.toHaveTextContent("/private");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("No current recommendations");
    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[0][0]?.aborted).toBe(true);
  });
  it("aborts on unmount and ignores a late completion", async () => {
    let finish!: (items: Recommendation[]) => void;
    const get = vi.spyOn(api, "recommendations").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const first = render(<RecommendationsDashboard />);
    const signal = get.mock.calls[0][0];
    first.unmount();
    expect(signal?.aborted).toBe(true);
    get.mockResolvedValueOnce([]);
    render(<RecommendationsDashboard />);
    await screen.findByText("No current recommendations");
    await act(async () => { finish(mockRecommendations); });
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});
