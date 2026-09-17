import type { BetSide, FixtureInput, MarketInput, MarketType, TeamSide } from "./api/types";

export interface ParsedSportsbookInput {
  fixture: Partial<FixtureInput>;
  markets: MarketInput[];
  errors: string[];
}

const COMPETITION_PATTERNS = [
  { code: "E1", pattern: /\b(?:english\s+)?championship\b|\bE1\b/i },
  { code: "SP2", pattern: /\b(?:la\s*liga\s*2|laliga\s*2|segunda(?:\s+divisi[oó]n)?|SP2)\b/i },
] as const;

const FIXTURE_PATTERN = /^(.+?)\s+(?:vs?\.?|versus)\s+(.+)$/i;
const DATE_PATTERN = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/;
const MARKET_PATTERN = /^(.+?)\s+(?:team\s+)?corners?\s+(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+-]\d+)$/i;
const MATCH_TOTAL_PATTERN = /^(?:match\s+)?(?:total\s+)?corners?\s+(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+-]\d+)$/i;

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

function side(value: string): BetSide {
  return value.toLocaleLowerCase().startsWith("o") ? "OVER" : "UNDER";
}

function validMarketValues(line: number, odds: number): boolean {
  return Number.isFinite(line) && line >= 0 && Number.isInteger(line * 2)
    && Number.isInteger(odds) && (odds <= -100 || odds >= 100);
}

function market(
  marketType: MarketType,
  teamSide: TeamSide | null,
  betSide: BetSide,
  line: number,
  odds: number,
): MarketInput {
  return {
    client_market_id: crypto.randomUUID(),
    market_type: marketType,
    team_side: teamSide,
    side: betSide,
    line,
    american_odds: odds,
  };
}

export function parseSportsbookInput(text: string): ParsedSportsbookInput {
  const lines = text.split(/\r?\n/).map(cleanLine).filter(Boolean);
  const errors: string[] = [];
  const parsedMarkets: MarketInput[] = [];
  let competition: string | undefined;
  let date: string | undefined;
  let homeTeam: string | undefined;
  let awayTeam: string | undefined;
  let primaryTeamSide: TeamSide | null = null;

  for (const line of lines) {
    const competitionMatch = COMPETITION_PATTERNS.find(({ pattern }) => pattern.test(line));
    if (competitionMatch && !competition) {
      competition = competitionMatch.code;
      continue;
    }

    const dateMatch = line.match(DATE_PATTERN);
    if (dateMatch && !date) {
      const [, year, month, day] = dateMatch;
      date = `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
      continue;
    }

    const fixtureMatch = line.match(FIXTURE_PATTERN);
    if (fixtureMatch && !homeTeam && !awayTeam) {
      homeTeam = fixtureMatch[1].trim();
      awayTeam = fixtureMatch[2].trim();
      continue;
    }

    const matchTotal = line.match(MATCH_TOTAL_PATTERN);
    if (matchTotal) {
      const lineValue = Number(matchTotal[2]);
      const odds = Number(matchTotal[3]);
      if (validMarketValues(lineValue, odds)) {
        parsedMarkets.push(market("MATCH_TOTAL", null, side(matchTotal[1]), lineValue, odds));
      } else {
        errors.push(`Check the line or odds: “${line}”`);
      }
      continue;
    }

    const teamTotal = line.match(MARKET_PATTERN);
    if (teamTotal) {
      if (!homeTeam || !awayTeam) {
        errors.push("Add “Home Team vs Away Team” before the market lines.");
        continue;
      }
      const teamName = normalized(teamTotal[1]);
      const home = normalized(homeTeam);
      const away = normalized(awayTeam);
      const namedTeamSide: TeamSide | null = teamName === home ? "HOME" : teamName === away ? "AWAY" : null;
      const teamSide: TeamSide | null = teamName === "opponent" && primaryTeamSide
        ? primaryTeamSide === "HOME" ? "AWAY" : "HOME"
        : namedTeamSide;
      const lineValue = Number(teamTotal[3]);
      const odds = Number(teamTotal[4]);
      if (!teamSide) {
        errors.push(`Could not match “${teamTotal[1]}” to ${homeTeam} or ${awayTeam}.`);
      } else if (!validMarketValues(lineValue, odds)) {
        errors.push(`Check the line or odds: “${line}”`);
      } else {
        parsedMarkets.push(market("TEAM_TOTAL", teamSide, side(teamTotal[2]), lineValue, odds));
        if (namedTeamSide && !primaryTeamSide) primaryTeamSide = namedTeamSide;
      }
      continue;
    }

    errors.push(`Could not read: “${line}”`);
  }

  if (!competition) errors.push("Include Championship or La Liga 2.");
  if (!date) errors.push("Include the fixture date as YYYY-MM-DD.");
  if (!homeTeam || !awayTeam) errors.push("Include the fixture as “Home Team vs Away Team”.");
  if (parsedMarkets.length === 0) errors.push("No corner markets were found.");

  return {
    fixture: { competition, date, home_team: homeTeam, away_team: awayTeam },
    markets: parsedMarkets,
    errors: [...new Set(errors)],
  };
}

export function formatMarket(marketInput: MarketInput, fixture: Partial<FixtureInput>): string {
  const subject = marketInput.market_type === "MATCH_TOTAL"
    ? "Match total"
    : marketInput.team_side === "HOME" ? fixture.home_team : fixture.away_team;
  const pick = marketInput.side === "OVER" ? "O" : "U";
  const odds = marketInput.american_odds > 0 ? `+${marketInput.american_odds}` : marketInput.american_odds;
  return `${subject} corners ${pick}${marketInput.line} ${odds}`;
}
