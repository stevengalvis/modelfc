import { describe, expect, it } from "vitest";
import { mockCapabilities } from "./api/mock";
import { MAX_MARKETS_PER_ANALYSIS, isCalendarDate, validateAnalysisInput } from "./analysis-input";
import { parseSportsbookInput } from "./parse-sportsbook-input";

describe("sportsbook parsing and validation", () => {
  function normalizedMarkets(text: string) {
    return parseSportsbookInput(text).markets.map((market) => ({
      source_text: market.source_text,
      market_type: market.market_type,
      team_side: market.team_side,
      side: market.side,
      line: market.line,
      american_odds: market.american_odds,
      parse_issue: market.parse_issue,
    })).sort((left, right) => left.source_text.localeCompare(right.source_text));
  }

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

  it.each(["E0", "E1", "SP1", "I1", "D1", "F1", "P1"])(
    "recognizes backend competition code %s without turning its header into a market",
    (code) => {
      const competitions = [{ code, name: `Configured ${code}` }];
      const parsed = parseSportsbookInput(`${code}\n2026-09-20\nCoventry City vs Birmingham City\nCoventry City O4.5 -145`, competitions);
      expect(parsed.fixture.competition).toBe(code);
      expect(parsed.markets).toHaveLength(1);
      expect(parsed.markets[0].source_text).toBe("Coventry City O4.5 -145");
    },
  );

  it("recognizes an exact backend competition name but not arbitrary non-code text", () => {
    const competitions = [{ code: "E0", name: "Premier League" }];
    expect(parseSportsbookInput("Premier League\n2026-09-20\nArsenal vs Chelsea\nArsenal O4.5 -110", competitions)
      .fixture.competition).toBe("E0");
    const unknown = parseSportsbookInput("Premier weekend picks\n2026-09-20\nArsenal vs Chelsea\nArsenal O4.5 -110", competitions);
    expect(unknown.fixture.competition).toBe("");
    expect(unknown.markets[0]).toMatchObject({
      source_text: "Premier weekend picks",
      parse_issue: expect.stringMatching(/could not determine/i),
    });
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

  it("resolves a home primary team's opponent identically in either row order", () => {
    const header = "Championship\n2026-09-20\nCoventry City vs Birmingham City\n";
    const namedFirst = normalizedMarkets(header + "Coventry City O4.5 -145\nOpponent O3.5 -120");
    const opponentFirst = normalizedMarkets(header + "Opponent O3.5 -120\nCoventry City O4.5 -145");
    expect(opponentFirst).toEqual(namedFirst);
    expect(namedFirst.find((market) => market.source_text.startsWith("Opponent")))
      .toMatchObject({ team_side: "AWAY", parse_issue: null });
  });

  it("resolves an away primary team's opponent identically in either row order", () => {
    const header = "Championship\n2026-09-20\nCoventry City vs Birmingham City\n";
    const namedFirst = normalizedMarkets(header + "Birmingham City O3.5 -120\nOpponent O4.5 -145");
    const opponentFirst = normalizedMarkets(header + "Opponent O4.5 -145\nBirmingham City O3.5 -120");
    expect(opponentFirst).toEqual(namedFirst);
    expect(namedFirst.find((market) => market.source_text.startsWith("Opponent")))
      .toMatchObject({ team_side: "HOME", parse_issue: null });
  });

  it("keeps opponent rows unresolved without one unambiguous named primary side", () => {
    const header = "Championship\n2026-09-20\nCoventry City vs Birmingham City\n";
    const noNamedTeam = parseSportsbookInput(header + "Opponent O3.5 -120");
    expect(noNamedTeam.markets[0]).toMatchObject({ team_side: "", parse_issue: expect.stringMatching(/ambiguous/i) });

    const contradictory = parseSportsbookInput(header
      + "Coventry City O4.5 -145\nOpponent O3.5 -120\nBirmingham City U4.5 -110");
    expect(contradictory.markets[1]).toMatchObject({
      source_text: "Opponent O3.5 -120",
      team_side: "",
      parse_issue: expect.stringMatching(/both fixture teams/i),
    });
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

  it.each([
    ["no blank", "E1\n2026-09-20\nCoventry City vs Birmingham City\nCoventry City O4.5 -145"],
    ["one decorative blank", "E1\n2026-09-20\nCoventry City vs Birmingham City\n\nCoventry City O4.5 -145"],
    ["multiple decorative blanks", "E1\n2026-09-20\nCoventry City vs Birmingham City\n\n\n\nCoventry City O4.5 -145"],
    ["a blank between markets", "E1\n2026-09-20\nCoventry City vs Birmingham City\nCoventry City O4.5 -145\n\nOpponent U3.5 -125"],
  ])("keeps fixture and markets together with %s", (_label, text) => {
    const parsed = parseSportsbookInput(text);
    expect(parsed.blocks).toHaveLength(1);
    expect(parsed.fixture).toMatchObject({ competition: "E1", home_team: "Coventry City", away_team: "Birmingham City" });
    expect(parsed.warnings).toEqual([]);
    expect(parsed.markets.every((market) => market.parse_issue === null)).toBe(true);
  });

  it("keeps two fixtures separated by whitespace as distinct blocks without repeated competition headers", () => {
    const parsed = parseSportsbookInput(`E1
2026-09-20
Coventry City vs Birmingham City
Coventry City O4.5 -145

2026-09-21
Millwall vs Watford
Millwall O4.5 -110`);
    expect(parsed.blocks).toHaveLength(2);
    expect(parsed.blocks.map((block) => block.fixture.home_team)).toEqual(["Coventry City", "Millwall"]);
    expect(parsed.blocks.map((block) => block.fixture.competition)).toEqual(["E1", "E1"]);
    expect(parsed.blocks[1].markets).toHaveLength(1);
  });

  it("inherits one active competition across three dated fixtures and decorative blanks", () => {
    const parsed = parseSportsbookInput(`E1

2026-09-20
Coventry City vs Birmingham City

Coventry City O4.5 -145


2026-09-21
Millwall vs Watford
Millwall O4.5 -110

2026-09-22
Derby vs Hull

Derby O4.5 -105`);
    expect(parsed.blocks).toHaveLength(3);
    expect(parsed.blocks.map((block) => block.fixture.competition)).toEqual(["E1", "E1", "E1"]);
    expect(parsed.blocks.every((block) => block.warnings.length === 0)).toBe(true);
  });

  it("inherits the active competition across undated fixture blocks", () => {
    const parsed = parseSportsbookInput(`E1
Coventry City vs Birmingham City
Coventry City O4.5 -145

Millwall vs Watford
Millwall O4.5 -110`);
    expect(parsed.blocks).toHaveLength(2);
    expect(parsed.blocks.map((block) => block.fixture.competition)).toEqual(["E1", "E1"]);
    expect(parsed.blocks.map((block) => block.fixture.date)).toEqual(["", ""]);
  });

  it("changes active competition only when a later explicit header replaces it", () => {
    const parsed = parseSportsbookInput(`E1
2026-09-20
Coventry City vs Birmingham City
Coventry City O4.5 -145

E1
2026-09-21
Millwall vs Watford
Millwall O4.5 -110

SP1
2026-09-22
Barcelona vs Sevilla
Barcelona O5.5 -120

2026-09-23
Valencia vs Getafe
Valencia O4.5 -115`);
    expect(parsed.blocks).toHaveLength(4);
    expect(parsed.blocks.map((block) => block.fixture.competition)).toEqual(["E1", "E1", "SP1", "SP1"]);
  });

  it("leaves every fixture unresolved when no competition context exists", () => {
    const parsed = parseSportsbookInput(`2026-09-20
Coventry City vs Birmingham City
Coventry City O4.5 -145

2026-09-21
Millwall vs Watford
Millwall O4.5 -110`);
    expect(parsed.blocks).toHaveLength(2);
    expect(parsed.blocks.map((block) => block.fixture.competition)).toEqual(["", ""]);
    expect(parsed.blocks.every((block) => block.warnings.some((warning) => /competition was not recognized/i.test(warning)))).toBe(true);
  });

  it("keeps fixtures with repeated competition headers as distinct blocks", () => {
    const parsed = parseSportsbookInput(`E1
2026-09-20
Coventry City vs Birmingham City
Coventry City O4.5 -145

E1
2026-09-21
Millwall vs Watford
Millwall O4.5 -110`);
    expect(parsed.blocks).toHaveLength(2);
    expect(parsed.blocks.map((block) => block.fixture.competition)).toEqual(["E1", "E1"]);
    expect(parsed.blocks.every((block) => block.warnings.length === 0)).toBe(true);
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
