import { describe, expect, it } from "vitest";
import { mockCapabilities } from "./api/mock";
import { MAX_MARKETS_PER_ANALYSIS, isCalendarDate, validateAnalysisInput } from "./analysis-input";
import { parseSportsbookInput } from "./parse-sportsbook-input";

describe("sportsbook parsing and validation", () => {
  it("parses the reproduced Championship shorthand without guessing", () => {
    const parsed = parseSportsbookInput(`Championship
2026-09-20
Coventry City vs Birmingham City
Coventry City O4.5 -145
Opponent O3.5 -120
Total U10.5 -125`);
    expect(parsed.fixture).toEqual({ competition: "E1", date: "2026-09-20", home_team: "Coventry City", away_team: "Birmingham City" });
    expect(parsed.markets.map(({ market_type, team_side, side, line, american_odds }) => ({ market_type, team_side, side, line, american_odds }))).toEqual([
      { market_type: "TEAM_TOTAL", team_side: "HOME", side: "OVER", line: "4.5", american_odds: "-145" },
      { market_type: "TEAM_TOTAL", team_side: "AWAY", side: "OVER", line: "3.5", american_odds: "-120" },
      { market_type: "MATCH_TOTAL", team_side: "", side: "UNDER", line: "10.5", american_odds: "-125" },
    ]);
  });

  it("preserves ambiguous and unsupported text with a correction reason", () => {
    const parsed = parseSportsbookInput(`Championship
2026-09-20
Coventry City vs Birmingham City
Opponent O3.5 -120
first-half corners 4.5 -110`);
    expect(parsed.markets).toHaveLength(2);
    expect(parsed.markets[0].source_text).toBe("Opponent O3.5 -120");
    expect(parsed.markets[0].parse_issue).toMatch(/ambiguous/i);
    expect(parsed.markets[1].source_text).toBe("first-half corners 4.5 -110");
    expect(parsed.markets[1].parse_issue).toMatch(/could not determine/i);
  });

  it("validates real dates, odds, line precision, and batch size without coercing blanks", () => {
    expect(isCalendarDate("2026-02-30")).toBe(false);
    const parsed = parseSportsbookInput(`Championship
2026-02-30
Coventry City vs Birmingham City
Coventry City O4 -145`);
    parsed.markets[0].line = "";
    parsed.markets[0].american_odds = "-99";
    const invalid = validateAnalysisInput(parsed.fixture, parsed.markets, mockCapabilities);
    expect(invalid.fixtureErrors.date).toMatch(/real date/i);
    expect(invalid.marketErrors[parsed.markets[0].client_market_id]).toMatchObject({ line: "Line is required." });
    expect(invalid.marketErrors[parsed.markets[0].client_market_id].american_odds).toMatch(/-100/);
    const tooMany = Array.from({ length: MAX_MARKETS_PER_ANALYSIS + 1 }, (_, index) => ({ ...parsed.markets[0], client_market_id: String(index), line: "4", american_odds: "-145" }));
    expect(validateAnalysisInput({ ...parsed.fixture, date: "2026-09-20" }, tooMany, mockCapabilities).batchError).toMatch(/at most 32/i);
  });
});
