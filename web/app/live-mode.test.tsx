import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const redirect = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ redirect }));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
  redirect.mockReset();
});

describe("site modes", () => {
  it("renders live recommendations empty without demo offers or Analyze navigation", async () => {
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_MODE", "live");
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_URL", "https://api.example.test/api/v1");
    const { emptyTrackRecord } = await import("@/lib/api/track-record");
    const empty = {...emptyTrackRecord, policy_version:"independent-value-strategies-v1", strategy:"higher_probability", scope:"current",
      stake_dollars:2,simulated:true,summary_scope:"page",profit_dollars:0,totals:emptyTrackRecord.summary,
      market_totals:[],total_profit_dollars:0,total:0,offset:0,limit:5,next_offset:null};
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(empty),{status:200})));
    const { default: Recommendations } = await import("./recommendations/page");
    render(<Recommendations />);
    await screen.findByText("No published picks for this strategy and filter yet. Historical subset picks are kept separately and are not reclassified.");
    expect(fetch).toHaveBeenCalledWith("https://api.example.test/api/v1/prospective/performance?view=strategies&strategy=higher_probability&scope=current&offset=0&limit=5", expect.objectContaining({cache:"no-store"}));
    expect(screen.getByText("Live API")).toBeInTheDocument();
    expect(screen.queryByText("Mock data")).not.toBeInTheDocument();
    expect(screen.queryByText(/Demo West Ham/)).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Analyze" })).not.toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(Array.from(navigation.querySelectorAll("a")).map((link) => link.textContent))
      .toEqual(["Next matches", "Recommendations", "Predictions", "Performance", "Teams", "BTTS Value"]);
  });

  it("keeps live BTTS empty and isolated from deterministic mock research", async () => {
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_MODE", "live");
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_URL", "https://api.example.test/api/v1");
    const fetcher = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const { default: BttsResearch } = await import("./research/btts/page");
    render(<BttsResearch />);
    await screen.findByText("No BTTS research comparisons yet");
    expect(fetcher).toHaveBeenCalledWith("https://api.example.test/api/v1/research/btts?competition=E1",
      expect.objectContaining({ cache: "no-store" }));
    expect(screen.queryByText(/Demo West Ham/)).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
  it("retains the Analyze workspace and home redirect in mock mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_MODE", "mock");
    const { default: Home } = await import("./page");
    Home();
    expect(redirect).toHaveBeenCalledWith("/analyze");
    const { default: Analyze } = await import("./analyze/page");
    render(<Analyze />);
    expect(screen.getByRole("link", { name: "Analyze" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Sportsbook fixture and corner markets" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Loading backend competitions…")).not.toBeInTheDocument());
  });

  it("routes live home to predictions and never mounts the analysis form", async () => {
    vi.stubEnv("NEXT_PUBLIC_MODELFC_API_MODE", "live");
    const { default: Home } = await import("./page");
    Home();
    expect(redirect).toHaveBeenCalledWith("/predictions");
    const { default: Analyze } = await import("./analyze/page");
    render(<Analyze />);
    expect(screen.getByText("Analyze unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Analyze" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /analyze all/i })).not.toBeInTheDocument();
    expect(screen.getByText("Live API")).toBeInTheDocument();
  });
});
