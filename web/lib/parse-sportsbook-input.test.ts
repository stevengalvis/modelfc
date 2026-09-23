import { describe, expect, it } from "vitest";
import { mockCapabilities } from "./api/mock";
import { MAX_MARKETS_PER_ANALYSIS, isCalendarDate, validateAnalysisInput } from "./analysis-input";
import { parseSportsbookInput } from "./parse-sportsbook-input";

describe("sportsbook parsing and validation", () => {
  it.each([
    ["E1", "bare E1"],
    ["Competition: E1", "prefixed E1"],
    ["Championship", "Championship"],
    ["English Championship", "English Championship"],
    ["Championship (E1)", "Championship with code"],
  ])("recognizes %s as E1 (%s)", (competitionLine) => {
    const parsed = parseSportsbookInput(`${competitionLine}\n2026-09-20\nCoventry City vs Birmingham City\nCoventry City O4.5 -145`);
    expect(parsed.fixture.competition).toBe("E1");
    expect(parsed.markets).toHaveLength(1);
  });

  it("does not treat E1 inside unrelated text as a competition", () => {
    const parsed = parseSportsbookInput("E1 Championship special\n2026-09-20\nCoventry City vs Birmingham City");
    expect(parsed.fixture.competition).toBe("");
    expect(parsed.markets).toHaveLength(1);
    expect(parsed.markets[0].source_text).toBe("E1 Championship special");
    expect(parsed.markets[0].parse_issue).toMatch(/could not determine/i);
  });

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

  it("keeps multiple pasted fixtures as source-preserving blocks", () => {
    const parsed = parseSportsbookInput(`E1
2026-09-20
Coventry City vs Birmingham City
Coventry City O4.5 -145

SP2
not-a-date
unresolved fixture text
Opponent O3.5 -120`);
    expect(parsed.blocks).toHaveLength(2);
    expect(parsed.blocks[0].source_text).toContain("Coventry City vs Birmingham City");
    expect(parsed.blocks[0].warnings).toEqual([]);
    expect(parsed.blocks[1].source_text).toContain("unresolved fixture text");
    expect(parsed.blocks[1].warnings.join(" ")).toMatch(/incomplete|unresolved/i);
    expect(parsed.blocks[1].markets[0].source_text).toBe("not-a-date");
    expect(parsed.blocks[1].markets.some((market) => market.source_text === "unresolved fixture text")).toBe(true);
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
