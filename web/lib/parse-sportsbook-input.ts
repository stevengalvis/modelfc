import type { BetSide, MarketType, TeamSide } from "./api/types";

// This is deliberately a lossless browser-side input adapter, not a second
// source of truth. The API owns canonical fixture/team resolution, grounding,
// and market eligibility; anything this adapter cannot identify remains in a
// block/row for correction instead of being guessed or discarded.

export interface EditableFixtureInput {
  competition: string;
  date: string;
  home_team: string;
  away_team: string;
}

export interface EditableMarketInput {
  client_market_id: string;
  source_text: string;
  market_type: MarketType | "";
  team_side: TeamSide | "";
  side: BetSide | "";
  line: string;
  american_odds: string;
  parse_issue: string | null;
}

export interface ParsedInputBlock {
  block_id: string;
  source_text: string;
  fixture: EditableFixtureInput;
  markets: EditableMarketInput[];
  warnings: string[];
}

export interface ParsedSportsbookInput {
  blocks: ParsedInputBlock[];
  warnings: string[];
  /** @deprecated Use blocks. Kept for callers migrating from the single-fixture model. */
  fixture: EditableFixtureInput;
  /** @deprecated Use blocks[0].markets. */
  markets: EditableMarketInput[];
}

const COMPETITION_PATTERNS = [
  { code: "E1", pattern: /^(?:(?:(?:league|competition)\s*[:=-]?\s*)?(?:english\s+)?championship(?:\s*\(?(?:E1)\)?)?|(?:competition\s*[:=-]?\s*)?E1)$/i },
  { code: "SP2", pattern: /^(?:(?:league|competition)\s*[:=-]?\s*)?(?:la\s*liga\s*2|laliga\s*2|segunda(?:\s+divisi[oó]n)?|SP2)$/i },
  { code: "SP1", pattern: /^(?:(?:league|competition)\s*[:=-]?\s*)?(?:la\s*liga|SP1)$/i },
] as const;

const FIXTURE_PATTERN = /^(.+?)\s+(?:vs?\.?|versus)\s+(.+)$/i;
const DATE_PATTERN = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/;
const EXPLICIT_TEAM_PATTERN = /^(.+?)\s+(?:team\s+)?corners?\s+(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+-]?\d+)?$/i;
const EXPLICIT_TOTAL_PATTERN = /^(?:match\s+)?(?:total\s+)?corners?\s+(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+-]?\d+)?$/i;
const SHORT_TOTAL_PATTERN = /^(?:match\s+)?total\s+(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+-]?\d+)?$/i;
const SHORT_TEAM_PATTERN = /^(.+?)\s+(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+-]?\d+)?$/i;

function cleanLine(line: string): string {
  return line
    .replace(/^[\s•*–—-]+/, "")
    .replace(/−/g, "-")
    .replace(/\s+/g, " ")
    .trim();
}

function normalized(value: string): string {
  return value.toLocaleLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function betSide(value: string): BetSide {
  return value.toLocaleLowerCase().startsWith("o") ? "OVER" : "UNDER";
}

function row(
  sourceText: string,
  values: Partial<Omit<EditableMarketInput, "client_market_id" | "source_text">>,
): EditableMarketInput {
  return {
    client_market_id: crypto.randomUUID(),
    source_text: sourceText,
    market_type: "",
    team_side: "",
    side: "",
    line: "",
    american_odds: "",
    parse_issue: null,
    ...values,
  };
}

function identifyTeam(
  label: string,
  fixture: EditableFixtureInput,
  primaryTeamSide: TeamSide | null,
): { teamSide: TeamSide | ""; issue: string | null; namedSide: TeamSide | null } {
  const name = normalized(label);
  const home = normalized(fixture.home_team);
  const away = normalized(fixture.away_team);
  const namedSide: TeamSide | null = name && name === home ? "HOME" : name && name === away ? "AWAY" : null;
  if (namedSide) return { teamSide: namedSide, issue: null, namedSide };
  if (name === "opponent" && primaryTeamSide) {
    return { teamSide: primaryTeamSide === "HOME" ? "AWAY" : "HOME", issue: null, namedSide: null };
  }
  if (name === "opponent") {
    return {
      teamSide: "",
      issue: "Opponent is ambiguous until another market identifies the primary team.",
      namedSide: null,
    };
  }
  return {
    teamSide: "",
    issue: `Choose whether “${label}” is the home or away team.`,
    namedSide: null,
  };
}

function splitBlocks(text: string): string[] {
  const sourceLines = text.split(/\r?\n/);
  const blocks: string[] = [];
  let current: string[] = [];
  const flush = () => {
    const source = current.join("\n").trim();
    if (source) blocks.push(source);
    current = [];
  };
  for (const rawLine of sourceLines) {
    const line = cleanLine(rawLine);
    const startsCompetition = COMPETITION_PATTERNS.some(({ pattern }) => pattern.test(line));
    // Blank lines are the normal separator. A repeated competition line also
    // starts a block so copied boards do not need perfect whitespace.
    const hasFixture = current.some((entry) => FIXTURE_PATTERN.test(cleanLine(entry)));
    // Ignore decorative whitespace inside an incomplete header, but use a
    // blank line after a fixture as the unambiguous block separator.
    if ((!line && hasFixture) || (startsCompetition && hasFixture)) flush();
    if (line) current.push(rawLine);
  }
  flush();
  return blocks;
}

function parseBlock(source: string, blockIndex: number): ParsedInputBlock {
  const lines = source.split(/\r?\n/).map(cleanLine).filter(Boolean);
  const fixture: EditableFixtureInput = { competition: "", date: "", home_team: "", away_team: "" };
  const marketLines: string[] = [];

  for (const line of lines) {
    const competition = COMPETITION_PATTERNS.find(({ pattern }) => pattern.test(line));
    if (competition && !fixture.competition) {
      fixture.competition = competition.code;
      continue;
    }
    const date = line.match(DATE_PATTERN);
    if (date && !fixture.date) {
      fixture.date = `${date[1]}-${date[2].padStart(2, "0")}-${date[3].padStart(2, "0")}`;
      continue;
    }
    const match = line.match(FIXTURE_PATTERN);
    if (match && !fixture.home_team && !fixture.away_team) {
      fixture.home_team = match[1].trim();
      fixture.away_team = match[2].trim();
      continue;
    }
    marketLines.push(line);
  }

  const markets: EditableMarketInput[] = [];
  let primaryTeamSide: TeamSide | null = null;
  for (const line of marketLines) {
    const total = line.match(EXPLICIT_TOTAL_PATTERN) ?? line.match(SHORT_TOTAL_PATTERN);
    if (total) {
      markets.push(row(line, {
        market_type: "MATCH_TOTAL",
        side: betSide(total[1]),
        line: total[2],
        american_odds: total[3] ?? "",
        parse_issue: total[3] ? null : "American odds are missing.",
      }));
      continue;
    }

    const explicit = line.match(EXPLICIT_TEAM_PATTERN);
    const shorthand = explicit ? null : line.match(SHORT_TEAM_PATTERN);
    const team = explicit ?? shorthand;
    if (team) {
      const identity = identifyTeam(team[1].trim(), fixture, primaryTeamSide);
      markets.push(row(line, {
        market_type: "TEAM_TOTAL",
        team_side: identity.teamSide,
        side: betSide(team[2]),
        line: team[3],
        american_odds: team[4] ?? "",
        parse_issue: identity.issue ?? (team[4] ? null : "American odds are missing."),
      }));
      if (identity.namedSide && !primaryTeamSide) primaryTeamSide = identity.namedSide;
      continue;
    }

    markets.push(row(line, {
      parse_issue: "Could not determine the corner market. Choose its fields below or remove this row.",
    }));
  }

  if (markets.length === 0) {
    markets.push(row("No market text detected", {
      parse_issue: "Enter at least one corner market.",
    }));
  }
  const warnings: string[] = [];
  if (!fixture.competition) warnings.push("Competition was not recognized; choose a backend-reported competition.");
  if (!fixture.date || !fixture.home_team || !fixture.away_team) {
    warnings.push("Fixture details are incomplete; correct the fields before analysis.");
  }
  const unresolved = markets.filter((market) => market.parse_issue).length;
  if (unresolved) warnings.push(`${unresolved} market row${unresolved === 1 ? " is" : "s are"} unresolved and needs correction.`);
  return {
    block_id: `block-${blockIndex + 1}`,
    source_text: source,
    fixture,
    markets,
    warnings,
  };
}

export function parseSportsbookInput(text: string): ParsedSportsbookInput {
  const blocks = splitBlocks(text).map(parseBlock);
  if (blocks.length === 0) blocks.push(parseBlock("", 0));
  const warnings = blocks.flatMap((block) => block.warnings);
  return {
    blocks,
    warnings,
    // Compatibility aliases for integrations that have not migrated to blocks.
    fixture: blocks[0].fixture,
    markets: blocks[0].markets,
  };
}

export const competitionLabels: Record<string, string> = {
  E1: "Championship",
  SP2: "La Liga 2",
  SP1: "La Liga",
};
