import type { BetSide, CompetitionCapability, MarketType, TeamSide } from "./api/types";

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

function marketNeedsCorrection(market: EditableMarketInput): boolean {
  return Boolean(market.parse_issue
    || !market.market_type
    || (market.market_type === "TEAM_TOTAL" && !market.team_side)
    || !market.side
    || !market.line.trim()
    || !market.american_odds.trim());
}

/** Derive parser-level warnings from the current editable values. */
export function deriveBlockWarnings(block: Pick<ParsedInputBlock, "fixture" | "markets">): string[] {
  const warnings: string[] = [];
  if (!block.fixture.competition.trim()) {
    warnings.push("Competition was not recognized; choose a backend-reported competition.");
  }
  if (!block.fixture.date.trim() || !block.fixture.home_team.trim() || !block.fixture.away_team.trim()) {
    warnings.push("Fixture details are incomplete; correct the fields before analysis.");
  }
  const unresolved = block.markets.filter(marketNeedsCorrection).length;
  if (unresolved) warnings.push(`${unresolved} market row${unresolved === 1 ? " is" : "s are"} unresolved and needs correction.`);
  return warnings;
}

const COMPETITION_PATTERNS = [
  { code: "E1", pattern: /^(?:(?:(?:league|competition)\s*[:=-]?\s*)?(?:english\s+)?championship(?:\s*\(?(?:E1)\)?)?|(?:competition\s*[:=-]?\s*)?E1)$/i },
  { code: "SP2", pattern: /^(?:(?:league|competition)\s*[:=-]?\s*)?(?:la\s*liga\s*2|laliga\s*2|segunda(?:\s+divisi[oó]n)?|SP2)$/i },
  { code: "SP1", pattern: /^(?:(?:league|competition)\s*[:=-]?\s*)?(?:la\s*liga|SP1)$/i },
] as const;

const COMPETITION_CODE_PATTERN = /^([A-Z]{1,3}\d{1,2})$/i;
const COMPETITION_PREFIX_PATTERN = /^(?:league|competition)(?:\s*[:=-]\s*|\s+)(.+)$/i;

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

function competitionCode(
  line: string,
  competitions: readonly Pick<CompetitionCapability, "code" | "name">[],
): string | null {
  const known = COMPETITION_PATTERNS.find(({ pattern }) => pattern.test(line));
  if (known) return known.code;
  const candidate = line.match(COMPETITION_PREFIX_PATTERN)?.[1] ?? line;
  const configured = competitions.find(({ code, name }) =>
    normalized(candidate) === normalized(code) || normalized(candidate) === normalized(name));
  if (configured) return configured.code.trim().toUpperCase();
  const code = candidate.match(COMPETITION_CODE_PATTERN)?.[1];
  return code ? code.toUpperCase() : null;
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
  contradictoryPrimarySides = false,
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
      issue: contradictoryPrimarySides
        ? "Opponent is ambiguous because named markets identify both fixture teams."
        : "Opponent is ambiguous until another market identifies the primary team.",
      namedSide: null,
    };
  }
  return {
    teamSide: "",
    issue: `Choose whether “${label}” is the home or away team.`,
    namedSide: null,
  };
}

function splitBlocks(
  text: string,
  competitions: readonly Pick<CompetitionCapability, "code" | "name">[],
): string[] {
  const sourceLines = text.split(/\r?\n/);
  const blocks: string[] = [];
  let current: string[] = [];
  let pendingSeparator = false;
  const flush = () => {
    const source = current.join("\n").trim();
    if (source) blocks.push(source);
    current = [];
  };
  const nextNonblankLine = (start: number): string | null => {
    for (let index = start; index < sourceLines.length; index += 1) {
      const line = cleanLine(sourceLines[index]);
      if (line) return line;
    }
    return null;
  };
  for (let index = 0; index < sourceLines.length; index += 1) {
    const rawLine = sourceLines[index];
    const line = cleanLine(rawLine);
    if (!line) {
      if (current.length > 0) pendingSeparator = true;
      continue;
    }
    const startsCompetition = competitionCode(line, competitions) !== null;
    const hasFixture = current.some((entry) => FIXTURE_PATTERN.test(cleanLine(entry)));
    const startsFixture = FIXTURE_PATTERN.test(line);
    const startsDatedFixture = DATE_PATTERN.test(line)
      && Boolean(nextNonblankLine(index + 1)?.match(FIXTURE_PATTERN));
    // Whitespace is only a pending separator. It becomes a boundary when the
    // next content starts a new fixture/header, never merely because markets
    // are visually separated from their fixture or from one another.
    if (hasFixture && (startsCompetition || startsFixture
      || (pendingSeparator && startsDatedFixture))) flush();
    current.push(rawLine);
    pendingSeparator = false;
  }
  flush();
  return blocks;
}

function parseBlock(
  source: string,
  blockIndex: number,
  competitions: readonly Pick<CompetitionCapability, "code" | "name">[],
): ParsedInputBlock {
  const lines = source.split(/\r?\n/).map(cleanLine).filter(Boolean);
  const fixture: EditableFixtureInput = { competition: "", date: "", home_team: "", away_team: "" };
  const marketLines: string[] = [];

  for (const line of lines) {
    const competition = competitionCode(line, competitions);
    if (competition && !fixture.competition) {
      fixture.competition = competition;
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
  const namedPrimarySides = new Set<TeamSide>();
  for (const line of marketLines) {
    if ((line.match(EXPLICIT_TOTAL_PATTERN) ?? line.match(SHORT_TOTAL_PATTERN))) continue;
    const team = line.match(EXPLICIT_TEAM_PATTERN) ?? line.match(SHORT_TEAM_PATTERN);
    if (!team) continue;
    const identity = identifyTeam(team[1].trim(), fixture, null);
    if (identity.namedSide) namedPrimarySides.add(identity.namedSide);
  }
  const contradictoryPrimarySides = namedPrimarySides.size > 1;
  const primaryTeamSide = namedPrimarySides.size === 1 ? [...namedPrimarySides][0] : null;
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
      const identity = identifyTeam(team[1].trim(), fixture, primaryTeamSide, contradictoryPrimarySides);
      markets.push(row(line, {
        market_type: "TEAM_TOTAL",
        team_side: identity.teamSide,
        side: betSide(team[2]),
        line: team[3],
        american_odds: team[4] ?? "",
        parse_issue: identity.issue ?? (team[4] ? null : "American odds are missing."),
      }));
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
  const block = {
    block_id: `block-${blockIndex + 1}`,
    source_text: source,
    fixture,
    markets,
    warnings: [] as string[],
  };
  block.warnings = deriveBlockWarnings(block);
  return block;
}

export function parseSportsbookInput(
  text: string,
  competitions: readonly Pick<CompetitionCapability, "code" | "name">[] = [],
): ParsedSportsbookInput {
  const blocks = splitBlocks(text, competitions).map((source, index) => parseBlock(source, index, competitions));
  if (blocks.length === 0) blocks.push(parseBlock("", 0, competitions));
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
