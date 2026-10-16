import { ModelFCApiError } from "./errors";
import { decodeRecommendations } from "./recommendations";
import { record, finite, timestamp, profit as oddsProfit } from "./prospective";
import type { Recommendation, ShortlistRanking } from "./types";

export interface TrackSummary {
  picks: number; unique_fixtures: number; settled: number; wins: number; losses: number;
  pushes: number; voids: number; pending: number; profit_units: number; win_rate: number | null; roi: number | null;
}
export interface BttsPublishedOffer {
  market_type: "BTTS"; side: "YES" | "NO"; competition: "E1";
  provider: string; provider_fixture_id: string; home_team: string; away_team: string;
  kickoff_utc: string; forecast_id: string; comparison_id: string; observation_id: string;
  bookmaker: "draftkings" | "fanduel"; american_odds: number; decimal_odds: number;
  model_probability: number; sportsbook_implied_probability: number;
  no_vig_market_probability: number; no_vig_probability_edge: number; expected_profit: number;
  retrieved_at_utc: string; availability_checked_at_utc: string; observation_age_seconds: number;
  policy_version: "btts-paired-value-v1";
}
export type PublishedOffer = (Recommendation & { opportunity_id: string }) | BttsPublishedOffer;
export interface Publication {
  publication_id: string; published_at_utc: string; forecast_created_at_utc: string;
  ranking_views: ShortlistRanking[]; offer: PublishedOffer;
  qualification_policy: { version: string; minimum_american_odds: number; minimum_no_vig_edge: number };
  result: "WIN" | "LOSS" | "PUSH" | "PENDING"; profit_units: number | null;
}
export interface TrackRecord {
  policy_version: string; void_settlement_supported: false; summary: TrackSummary;
  by_view: Record<ShortlistRanking, TrackSummary>;
  by_league_market: { competition: string; market_type: string; summary: TrackSummary }[];
  publications: Publication[];
}
const blank: TrackSummary = { picks: 0, unique_fixtures: 0, settled: 0, wins: 0, losses: 0,
  pushes: 0, voids: 0, pending: 0, profit_units: 0, win_rate: null, roi: null };
export const emptyTrackRecord: TrackRecord = { policy_version: "first-team-total-value-v1",
  void_settlement_supported: false, summary: blank,
  by_view: { best_value: blank, higher_probability: blank }, by_league_market: [], publications: [] };
const hash = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const close = (a: number | null, b: number | null) => a === null || b === null ? a === b : Math.abs(a-b) < 1e-8;
function validSummary(value: unknown, rows: Publication[]) {
  if (!record(value)) return false;
  for (const k of ["picks", "unique_fixtures", "settled", "wins", "losses", "pushes", "voids", "pending"]) {
    if (!Number.isSafeInteger(value[k]) || Number(value[k]) < 0) return false;
  }
  if (!finite(value.profit_units) || !(value.win_rate === null || finite(value.win_rate)) || !(value.roi === null || finite(value.roi))) return false;
  const s = value as unknown as TrackSummary;
  const wins = rows.filter(r => r.result === "WIN").length, losses = rows.filter(r => r.result === "LOSS").length;
  const pushes = rows.filter(r => r.result === "PUSH").length, settled = wins+losses+pushes;
  const profit = rows.reduce((sum, r) => sum+(r.profit_units ?? 0), 0);
  return s.picks === rows.length && s.unique_fixtures === new Set(rows.map(r =>
    JSON.stringify([r.offer.competition, r.offer.provider, r.offer.provider_fixture_id, Date.parse(r.offer.kickoff_utc)]))).size
    && s.wins === wins && s.losses === losses && s.pushes === pushes && s.settled === settled
    && s.voids === 0 && s.pending === rows.length-settled && close(s.profit_units, profit)
    && close(s.win_rate, wins+losses ? wins/(wins+losses) : null) && close(s.roi, settled ? profit/settled : null);
}
export function decodeTrackRecord(value: unknown): TrackRecord {
  const fail = (): never => { throw new ModelFCApiError("Published recommendation record is unavailable or invalid.", "TRACK_RECORD_CONTRACT_MISMATCH", false); };
  if (!record(value) || !["first-team-total-value-v1", "first-published-value-v2"].includes(value.policy_version as string) || value.void_settlement_supported !== false
    || !Array.isArray(value.publications) || !record(value.by_view) || !Array.isArray(value.by_league_market)) return fail();
  for (const r of value.publications) {
    if (!record(r) || !hash(r.publication_id) || !timestamp(r.published_at_utc) || !timestamp(r.forecast_created_at_utc)
      || !record(r.offer)
      || !Array.isArray(r.ranking_views) || !record(r.qualification_policy)
      || r.qualification_policy.version !== (r.offer.market_type === "BTTS" ? "btts-paired-value-v1" : "team-total-no-vig-v1")
      || r.qualification_policy.minimum_american_odds !== -200
      || r.qualification_policy.minimum_no_vig_edge !== .05) return fail();
    if (r.offer.market_type === "BTTS") {
      if (!validBtts(r.offer) || r.result === "PUSH") return fail();
    } else {
      if (typeof r.offer.opportunity_id !== "string" || !/^[0-9a-f]{32}$/.test(r.offer.opportunity_id)) return fail();
      const { opportunity_id: _, ...offer } = r.offer;
      try { decodeRecommendations([offer]); } catch { return fail(); }
    }
    const o = r.offer as unknown as PublishedOffer;
    const views = ["best_value", ...(o.model_probability >= .65 ? ["higher_probability"] : [])];
    if (JSON.stringify(r.ranking_views) !== JSON.stringify(views) || o.american_odds < -200 || o.no_vig_probability_edge < .05-1e-12
      || Date.parse(r.forecast_created_at_utc as string) > Date.parse(r.published_at_utc as string)
      || Date.parse(o.availability_checked_at_utc) > Date.parse(r.published_at_utc as string)
      || Date.parse(r.published_at_utc as string) >= Date.parse(o.kickoff_utc)) return fail();
    const win = oddsProfit(o.american_odds);
    if (r.result === "PENDING" ? r.profit_units !== null :
      !["WIN", "LOSS", "PUSH"].includes(r.result as string) || !finite(r.profit_units)
      || !close(r.profit_units as number, r.result === "WIN" ? win : r.result === "LOSS" ? -1 : 0)) return fail();
  }
  const report = value as unknown as TrackRecord;
  if (new Set(report.publications.map(r => r.publication_id)).size !== report.publications.length
    || new Set(report.publications.map(r => JSON.stringify([r.offer.competition, r.offer.provider, r.offer.provider_fixture_id,
      Date.parse(r.offer.kickoff_utc), r.offer.market_type, r.offer.market_type === "BTTS" ? null : r.offer.team_side]))).size !== report.publications.length
    || !validSummary(report.summary, report.publications)) return fail();
  for (const view of ["best_value", "higher_probability"] as const) {
    if (!validSummary(report.by_view[view], report.publications.filter(r => r.ranking_views.includes(view)))) return fail();
  }
  const groups = new Set(report.publications.map(r => JSON.stringify([r.offer.competition,r.offer.market_type])));
  if (groups.size !== report.by_league_market.length) return fail();
  for (const group of report.by_league_market) {
    if (!record(group) || !groups.delete(JSON.stringify([group.competition,group.market_type]))
      || !validSummary(group.summary, report.publications.filter(r => r.offer.competition === group.competition && r.offer.market_type === group.market_type))) return fail();
  }
  return report;
}

function validBtts(v: Record<string, unknown>): boolean {
  if (v.competition !== "E1" || !["YES", "NO"].includes(v.side as string)
    || !["draftkings", "fanduel"].includes(v.bookmaker as string) || v.policy_version !== "btts-paired-value-v1"
    || !["forecast_id", "comparison_id", "observation_id"].every(k => hash(v[k]))
    || !["provider", "provider_fixture_id", "home_team", "away_team"].every(k => typeof v[k] === "string" && v[k].length > 0)
    || !["kickoff_utc", "retrieved_at_utc", "availability_checked_at_utc"].every(k => timestamp(v[k]))
    || !["model_probability", "no_vig_market_probability", "sportsbook_implied_probability"].every(k => finite(v[k]) && v[k] >= 0 && v[k] <= 1)
    || !["decimal_odds", "no_vig_probability_edge", "expected_profit", "observation_age_seconds"].every(k => finite(v[k]))
    || !Number.isSafeInteger(v.american_odds) || Math.abs(Number(v.american_odds)) < 100) return false;
  const o = v as unknown as BttsPublishedOffer;
  const profit = oddsProfit(o.american_odds);
  return o.decimal_odds > 1 && Math.abs(o.decimal_odds-1-profit) <= .005
    && o.expected_profit > 0 && o.observation_age_seconds >= 0
    && close(o.expected_profit, o.model_probability*o.decimal_odds-1)
    && close(o.sportsbook_implied_probability, 1/o.decimal_odds)
    && close(o.no_vig_probability_edge, o.model_probability-o.no_vig_market_probability)
    && o.retrieved_at_utc === o.availability_checked_at_utc
    && Date.parse(o.retrieved_at_utc) < Date.parse(o.kickoff_utc);
}
