import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import wholeLineFixture from "../../tests/fixtures/api_v1/analysis_whole_line.json";
import mixedFixture from "../../tests/fixtures/api_v1/analysis.json";
import type { AnalysisResponse, AnalyzedMarket } from "@/lib/api/types";
import { AnalysisResults } from "./analysis-results";

function analysis(id: string): AnalysisResponse {
  return { ...structuredClone(wholeLineFixture), analysis_id: id } as AnalysisResponse;
}

describe("AnalysisResults", () => {
  it("uses the rendered request identity when duplicate results share an analysis ID", () => {
    render(<>
      <AnalysisResults analysis={analysis("shared-analysis")} instanceId="block-1:chunk-1" />
      <AnalysisResults analysis={analysis("shared-analysis")} instanceId="block-2:chunk-1" />
    </>);

    const sections = screen.getAllByRole("region");
    const headingIds = sections.map((section) => section.getAttribute("aria-labelledby"));
    expect(headingIds).toEqual([
      "results-title-shared-analysis-block-1%3Achunk-1",
      "results-title-shared-analysis-block-2%3Achunk-1",
    ]);
    expect(new Set(headingIds).size).toBe(2);
    for (const section of sections) {
      const headingId = section.getAttribute("aria-labelledby")!;
      expect(document.getElementById(headingId)).not.toBeNull();
    }
  });

  it("falls back to fixture teams only when a team-total result has no team", () => {
    const value = analysis("market-labels");
    const supported = structuredClone(value.markets[0]);
    const unsupported = structuredClone(mixedFixture.markets[1]);
    value.markets = [
      { ...unsupported, client_market_id: "home", market_type: "TEAM_TOTAL", team_side: "HOME", team: null, line: 4 },
      { ...unsupported, client_market_id: "away", market_type: "TEAM_TOTAL", team_side: "AWAY", team: null, line: 3.5 },
      { ...supported, client_market_id: "named", market_type: "TEAM_TOTAL", team_side: "HOME", team: "Stored team", line: 5.5 },
      { ...unsupported, client_market_id: "match", market_type: "MATCH_TOTAL", team_side: null, team: null, line: 9.5 },
    ] as AnalyzedMarket[];

    render(<AnalysisResults analysis={value} instanceId="market-labels:chunk-1" />);

    expect(screen.getByText("Birmingham O4")).toBeInTheDocument();
    expect(screen.getByText("Millwall O3.5")).toBeInTheDocument();
    expect(screen.getByText("Stored team O5.5")).toBeInTheDocument();
    expect(screen.getByText("Match O9.5")).toBeInTheDocument();
    expect(screen.queryByText(/null O/)).not.toBeInTheDocument();
  });
});
