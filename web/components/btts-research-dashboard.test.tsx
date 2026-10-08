import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BttsResearchPage from "@/app/research/btts/page";
import { api } from "@/lib/api/client";
import { mockBttsResearch } from "@/lib/api/mock-btts-research";
import type { BttsResearchComparison } from "@/lib/api/types";
import { BttsResearchDashboard } from "./btts-research-dashboard";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("BTTS Value workspace", () => {
  it("renders navigation, E1 selector, research framing and paired DK/FD comparisons", async () => {
    const get = vi.spyOn(api, "bttsResearch").mockResolvedValue(structuredClone(mockBttsResearch));
    render(<BttsResearchPage />);
    expect(screen.getByRole("heading", { level: 1, name: "BTTS Value" })).toBeInTheDocument();
    expect(screen.getByText("Compare Zeno's experimental BTTS probabilities with sportsbook prices.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "BTTS Value" })).toHaveAttribute("aria-current", "page");
    const selector = screen.getByRole("combobox", { name: "League" });
    expect(within(selector).getAllByRole("option")).toHaveLength(1);
    expect(within(selector).getByRole("option", { name: "Championship (E1)" })).toBeEnabled();
    expect(get).toHaveBeenCalledWith("E1", expect.any(AbortSignal));
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Demo West Ham vs Demo QPR" })).toBeInTheDocument();
    expect(cards[0]).toHaveTextContent("DraftKings");
    expect(cards[1]).toHaveTextContent("FanDuel");
    for (const card of cards) {
      expect(within(card).getByRole("region", { name: "BTTS YES" })).toBeInTheDocument();
      expect(within(card).getByRole("region", { name: "BTTS NO" })).toBeInTheDocument();
      expect(card).toHaveTextContent("Research only");
      expect(card).toHaveTextContent("Current price");
    }
    expect(cards[0]).toHaveTextContent("+120");
    expect(cards[0]).toHaveTextContent("67.0%");
    expect(cards[0]).toHaveTextContent("44.3%");
    expect(cards[0]).toHaveTextContent("+22.7 pp");
    expect(cards[0]).toHaveTextContent("+$0.47 / $1");
    expect(within(cards[0]).getByText("Best current price")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Best current price")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it.each([
    ["STALE", "Stale observation"], ["UNAVAILABLE", "Unavailable"],
    ["UNKNOWN", "Current status unknown"], ["SUPERSEDED", "Superseded observation"],
    ["KICKED_OFF", "Historical · kicked off"], ["FUTURE_OBSERVATION", "Future-dated observation"],
  ] as const)("labels %s as research history rather than a current price", async (status, label) => {
    const item = structuredClone(mockBttsResearch[0]);
    item.current_status = status;
    item.observation_age_seconds = status === "FUTURE_OBSERVATION" ? null : 400;
    item.best_yes_price = false;
    vi.spyOn(api, "bttsResearch").mockResolvedValue([item]);
    render(<BttsResearchDashboard />);
    const card = await screen.findByRole("article");
    expect(card).toHaveTextContent(label);
    expect(card).toHaveTextContent("Research history only");
    expect(card).not.toHaveTextContent("Best current price");
  });

  it("renders the intentional empty response without mock bets", async () => {
    vi.spyOn(api, "bttsResearch").mockResolvedValue([]);
    render(<BttsResearchDashboard />);
    expect(await screen.findByRole("heading", { name: "No BTTS research comparisons yet" })).toBeInTheDocument();
    expect(screen.getByText("Comparisons will appear when Zeno captures eligible BTTS market observations.")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("preserves backend comparison order without client price sorting", async () => {
    vi.spyOn(api, "bttsResearch").mockResolvedValue(structuredClone(mockBttsResearch).reverse());
    render(<BttsResearchDashboard />);
    const cards = await screen.findAllByRole("article");
    expect(cards.map((card) => within(card).getByRole("heading", { level: 2 }).textContent))
      .toEqual(["FanDuel", "DraftKings"]);
  });

  it("shows loading, sanitizes failure details and retries", async () => {
    let finish!: (value: BttsResearchComparison[]) => void;
    const get = vi.spyOn(api, "bttsResearch")
      .mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    render(<BttsResearchDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading BTTS research");
    await act(async () => finish([]));
    get.mockRejectedValueOnce(new Error("SECRET /private/evidence"));
    fireEvent(window, new Event("focus"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load research comparisons");
    expect(alert).not.toHaveTextContent("SECRET");
    get.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("No BTTS research comparisons yet");
  });

  it("refreshes from the backend without reclassifying status in a client timer", async () => {
    vi.useFakeTimers();
    const stale = structuredClone(mockBttsResearch[0]);
    stale.current_status = "STALE";
    stale.observation_age_seconds = 301;
    stale.best_yes_price = false;
    vi.spyOn(api, "bttsResearch").mockResolvedValueOnce([mockBttsResearch[0]]).mockResolvedValueOnce([stale]);
    render(<BttsResearchDashboard />);
    await act(async () => {});
    expect(screen.getByText("Current price")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(59_999); });
    expect(screen.getByText("Current price")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByText("Stale observation")).toBeInTheDocument();
  });

  it("aborts on unmount and ignores a late response", async () => {
    let finish!: (value: BttsResearchComparison[]) => void;
    const get = vi.spyOn(api, "bttsResearch").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const view = render(<BttsResearchDashboard />);
    const signal = get.mock.calls[0][1];
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(mockBttsResearch));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});
