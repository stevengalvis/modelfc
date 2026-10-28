import { ModelFCApiError } from "./errors";
import { decodeRecommendations } from "./recommendations";
import { record, finite, timestamp, profit as oddsProfit } from "./prospective";
import type { Recommendation, ShortlistRanking } from "./types";

export interface TrackSummary {
  picks: number; unique_fixtures: number; settled: number; wins: number; losses: number;
  pushes: number; voids: number; pending: number; profit_units: number; win_rate: number | null; roi: number | null;
}
export interface BttsPublishedOffer {
  market_type: "BTTS"; side: "YES" | "NO"; competition: "E1" | "E0";
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
  epl_evidence?: { forecast_id: string; observation_id: string; qualification_sha256: string };
  qualification_policy: { version: string; minimum_american_odds: number; minimum_no_vig_edge: number };
  result: "WIN" | "LOSS" | "PUSH" | "PENDING" | "SIMULATION_VOID"; simulation_void?: { policy: string; record_hash: string; reason: string; recorded_at_utc: string }; profit_units: number | null;
}
export interface PublicationSelection {
  as_of_utc: string | null; competition: string | null; market: string | null;
  membership_basis: "recorded-publication-time"; historical_store_membership: "UNVERIFIED";
}
export interface PublicationFilters { asOf?: string; competition?: string; market?: string }
export function publicationSelection(filters: PublicationFilters): PublicationSelection {
  return { as_of_utc: filters.asOf || null, competition: filters.competition || null, market: filters.market || null,
    membership_basis: "recorded-publication-time", historical_store_membership: "UNVERIFIED" };
}
export function assertPublicationSelection(report: { selection?: PublicationSelection; publications: Publication[] }, filters: PublicationFilters) {
  const s = report.selection;
  const filtered = !!(filters.asOf || filters.competition || filters.market);
  const cutoff = filters.asOf ? Date.parse(filters.asOf) : null;
  if ((filtered && !s) || (s && (s.membership_basis !== "recorded-publication-time" || s.historical_store_membership !== "UNVERIFIED"
      || s.competition !== (filters.competition || null) || s.market !== (filters.market || null)
      || (cutoff === null ? s.as_of_utc !== null : !timestamp(s.as_of_utc) || Date.parse(s.as_of_utc!) !== cutoff)))
    || report.publications.some(r => (filters.competition && r.offer.competition !== filters.competition)
      || (filters.market && r.offer.market_type !== filters.market)
      || (cutoff !== null && (Date.parse(r.published_at_utc) > cutoff
        || (r.simulation_void && Date.parse(r.simulation_void.recorded_at_utc) > cutoff))))) {
    throw new ModelFCApiError("Publication history does not match the requested filters.", "TRACK_RECORD_CONTRACT_MISMATCH", false);
  }
}
export interface TrackRecord {
  selection?: PublicationSelection;
  policy_version: string; void_settlement_supported: false; simulation_void_supported?: boolean; summary: TrackSummary;
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
    && s.voids === rows.filter(r => r.result === "SIMULATION_VOID").length && s.pending === rows.filter(r => r.result === "PENDING").length && close(s.profit_units, profit)
    && close(s.win_rate, wins+losses ? wins/(wins+losses) : null) && close(s.roi, settled ? profit/settled : null);
}
export function decodeTrackRecord(value: unknown, independent = false): TrackRecord {
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
    if (r.offer.competition === "E0" && (!record(r.epl_evidence)
      || Object.keys(r.epl_evidence).length !== 3
      || !["forecast_id", "observation_id", "qualification_sha256"].every(k => hash((r.epl_evidence as Record<string, unknown>)[k])))) return fail();
    if (r.offer.market_type === "BTTS") {
      if (!validBtts(r.offer) || r.result === "PUSH") return fail();
    } else {
      if (typeof r.offer.opportunity_id !== "string" || !(r.offer.competition === "E0" ? /^[0-9a-f]{64}$/ : /^[0-9a-f]{32}$/).test(r.offer.opportunity_id)) return fail();
      const { opportunity_id: _, ...offer } = r.offer;
      try { decodeRecommendations([offer]); } catch { return fail(); }
    }
    const o = r.offer as unknown as PublishedOffer;
    const views = independent ? [r.strategy] : ["best_value", ...(o.model_probability >= .65 ? ["higher_probability"] : [])];
    if (independent && (r.schema_version !== 2 || !["best_value", "higher_probability"].includes(r.strategy as string)
      || r.strategy_policy !== "independent-value-strategies-v1" || !hash(r.selection_id)
      || !timestamp(r.policy_effective_at_utc) || Date.parse(r.policy_effective_at_utc as string) > Date.parse(r.published_at_utc as string)
      || r.strategy === "higher_probability" && o.model_probability < .65)) return fail();
    if (JSON.stringify(r.ranking_views) !== JSON.stringify(views) || o.american_odds < -200 || o.no_vig_probability_edge < .05-1e-12
      || Date.parse(r.forecast_created_at_utc as string) > Date.parse(r.published_at_utc as string)
      || Date.parse(o.availability_checked_at_utc) > Date.parse(r.published_at_utc as string)
      || Date.parse(r.published_at_utc as string) >= Date.parse(o.kickoff_utc)) return fail();
    if (r.result === "SIMULATION_VOID" && (value.simulation_void_supported !== true
      || !record(r.simulation_void) || r.simulation_void.policy !== "reviewed-simulation-void-v1"
      || !hash(r.simulation_void.record_hash) || !timestamp(r.simulation_void.recorded_at_utc)
      || typeof r.simulation_void.reason !== "string" || !r.simulation_void.reason.trim())) return fail();
    const win = oddsProfit(o.american_odds);
    if (r.result === "PENDING" ? r.profit_units !== null :
      !["WIN", "LOSS", "PUSH", "SIMULATION_VOID"].includes(r.result as string) || !finite(r.profit_units)
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
  if (!["E1", "E0"].includes(v.competition as string) || !["YES", "NO"].includes(v.side as string)
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
    && (o.competition === "E1" ? o.retrieved_at_utc === o.availability_checked_at_utc
      : Date.parse(o.retrieved_at_utc) <= Date.parse(o.availability_checked_at_utc)
        && Date.parse(o.availability_checked_at_utc)-Date.parse(o.retrieved_at_utc) <= 900_000)
    && Date.parse(o.retrieved_at_utc) < Date.parse(o.kickoff_utc);
}

export interface StrategyPick extends Publication {
  strategy: ShortlistRanking; strategy_policy: string; policy_effective_at_utc: string;
  selection_id: string; simulated_stake_dollars: 2; profit_dollars: number | null;
}
export interface StrategyPage {
  selection?: PublicationSelection;
  policy_version: string; strategy: ShortlistRanking; scope: "current" | "history";
  stake_dollars: 2; simulated: true; summary_scope: "page"; summary: TrackSummary;
  totals: TrackSummary; market_totals: TrackRecord["by_league_market"]; total_profit_dollars: number;
  profit_dollars: number; by_league_market: TrackRecord["by_league_market"];
  publications: StrategyPick[]; total: number; offset: number; limit: number; next_offset: number | null;
}
export function decodeStrategyPage(value: unknown): StrategyPage {
  const fail = (): never => { throw new ModelFCApiError("Strategy results are unavailable or invalid.", "STRATEGY_CONTRACT_MISMATCH", false); };
  if (!record(value) || value.policy_version !== "independent-value-strategies-v1"
    || !["best_value", "higher_probability"].includes(value.strategy as string)
    || !["current", "history"].includes(value.scope as string) || value.simulated !== true || value.stake_dollars !== 2
    || value.summary_scope !== "page" || !Array.isArray(value.publications)
    || !["total", "offset", "limit"].every(k => Number.isSafeInteger(value[k]) && Number(value[k]) >= 0)
    || Number(value.limit) < 1 || Number(value.limit) > 20 || value.publications.length > Number(value.limit)
    || value.publications.length > Number(value.total)) return fail();
  if (!validAggregate(value.totals, Number(value.total)) || !finite(value.total_profit_dollars)
    || !record(value.totals) || !close(value.total_profit_dollars, Number(value.totals.profit_units)*2)
    || !Array.isArray(value.market_totals) || !value.market_totals.every(g => record(g) && validAggregate(g.summary))
    || value.market_totals.reduce((n,g)=>n+g.summary.picks,0) !== value.total) return fail();
  const report = decodeTrackRecord({...value, policy_version:"first-published-value-v2", by_view: {
    best_value: value.strategy === "best_value" ? value.summary : blank,
    higher_probability: value.strategy === "higher_probability" ? value.summary : blank,
  }}, true);
  for (const row of value.publications) {
    if (row.strategy !== value.strategy || row.simulated_stake_dollars !== 2
      || !close(row.profit_dollars, row.profit_units === null ? null : row.profit_units*2)) return fail();
  }
  const expected = Number(value.offset)+value.publications.length < Number(value.total)
    ? Number(value.offset)+value.publications.length : null;
  if (value.next_offset !== expected || !finite(value.profit_dollars) || !close(value.profit_dollars, report.summary.profit_units*2)) return fail();
  return value as unknown as StrategyPage;
}

function validAggregate(v: unknown, count?: number): boolean {
  if (!record(v) || !["picks","unique_fixtures","settled","wins","losses","pushes","pending","voids"].every(k=>Number.isSafeInteger(v[k]) && Number(v[k])>=0)
    || !finite(v.profit_units) || !(v.roi===null || finite(v.roi)) || !(v.win_rate===null || finite(v.win_rate))) return false;
  const s=v as unknown as TrackSummary;
  return (count===undefined || s.picks===count) && s.unique_fixtures<=s.picks
    && s.settled===s.wins+s.losses+s.pushes && s.picks===s.settled+s.pending+s.voids
    && close(s.roi,s.settled?s.profit_units/s.settled:null)
    && close(s.win_rate,s.wins+s.losses?s.wins/(s.wins+s.losses):null);
}
