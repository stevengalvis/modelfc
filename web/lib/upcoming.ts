import type { ProspectivePrediction } from "./api/types";
import type { BttsPerformance } from "./api/btts-performance";

export type Identity = { competition: string; home_team: string; away_team: string; kickoff_utc: string };
export const fixtureKey = (row: Identity) => JSON.stringify([row.competition, row.home_team, row.away_team, Date.parse(row.kickoff_utc)]);
export type MarketForecast = { id: string; frozen: string; version: string; values: number[]; history?: string; research: boolean };
export type UpcomingFixture = Identity & { key: string; corner?: MarketForecast; btts?: MarketForecast };
const earlier = (a: MarketForecast, b?: MarketForecast) => !b || Date.parse(a.frozen) < Date.parse(b.frozen)
  || (Date.parse(a.frozen) === Date.parse(b.frozen) && a.id < b.id);

/** Exact identity, earliest freeze per market; books/cohorts never add cards. */
export function upcomingFixtures(predictions: ProspectivePrediction[], btts: BttsPerformance["forecasts"], now: number): UpcomingFixture[] {
  const fixtures = new Map<string, UpcomingFixture>();
  function add(row: Identity, market: "corner" | "btts", forecast: MarketForecast) {
    if (row.competition !== "E1" || Date.parse(row.kickoff_utc) <= now || Date.parse(forecast.frozen) > now) return;
    const key = fixtureKey(row);
    const card = fixtures.get(key) ?? { competition: row.competition, home_team: row.home_team,
      away_team: row.away_team, kickoff_utc: row.kickoff_utc, key };
    if (earlier(forecast, card[market])) card[market] = forecast;
    fixtures.set(key, card);
  }
  for (const p of predictions) {
    if (p.settlement_status !== "UPCOMING") continue;
    const market = p.prediction_type === "BTTS" ? "btts" : "corner";
    const values = market === "btts" ? [p.btts_yes_probability, p.btts_no_probability]
      : [p.expected_home_corners, p.expected_away_corners, p.expected_match_corners];
    if (!values.every((v): v is number => typeof v === "number" && Number.isFinite(v))) continue;
    add(p, market, { id: p.prediction_id, frozen: p.created_at_utc, version: p.model_version,
      values, history: p.latest_history_date, research: p.research_status === "RESEARCH_ONLY" || market === "btts" });
  }
  for (const p of btts) {
    if (p.status !== "UPCOMING") continue;
    add(p, "btts", { id: p.forecast_id, frozen: p.frozen_at_utc, version: p.model_version,
      values: [p.yes_probability, p.no_probability], research: true });
  }
  return [...fixtures.values()].sort((a,b) => Date.parse(a.kickoff_utc)-Date.parse(b.kickoff_utc) || a.key.localeCompare(b.key));
}

export function ageLabel(value: string, now: number): string {
  const minutes = Math.max(0, Math.floor((now-Date.parse(value))/60000));
  return minutes < 1 ? "less than a minute ago" : minutes < 60 ? `${minutes} min ago`
    : minutes < 1440 ? `${Math.floor(minutes/60)} hr ago` : `${Math.floor(minutes/1440)} days ago`;
}

/** Traverse both independent strategies. Incomplete reads are unknown, never empty. */
export async function loadUpcomingPicks(signal: AbortSignal) {
  const { api } = await import("./api/client");
  const pages = await Promise.all((["best_value", "higher_probability"] as const).map(async strategy => {
    const rows: import("./api/track-record").StrategyPick[] = [];
    let offset = 0;
    for (let pageNumber = 0; pageNumber < 200; pageNumber++) {
      signal.throwIfAborted();
      const page = await api.strategyPicks({ strategy, scope: "current", offset }, signal);
      rows.push(...page.publications);
      if (page.next_offset === null) return rows;
      if (page.next_offset <= offset) throw new Error("Non-advancing published picks page");
      offset = page.next_offset;
    }
    throw new Error("Published picks coverage exceeded read limit");
  }));
  return [...new Map(pages.flat().map(row => [row.publication_id, row])).values()];
}
