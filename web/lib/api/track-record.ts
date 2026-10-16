import { ModelFCApiError } from "./errors";
import { decodeRecommendations } from "./recommendations";
import { record, finite, timestamp } from "./prospective";
import type { Recommendation, ShortlistRanking } from "./types";

export interface TrackSummary {
  picks: number; unique_fixtures: number; settled: number; wins: number; losses: number;
  pushes: number; voids: number; pending: number; profit_units: number; win_rate: number | null; roi: number | null;
}
export interface Publication {
  publication_id: string; published_at_utc: string; forecast_created_at_utc: string;
  ranking_views: ShortlistRanking[]; offer: Recommendation & { opportunity_id: string };
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
  if (!record(value) || value.policy_version !== "first-team-total-value-v1" || value.void_settlement_supported !== false
    || !Array.isArray(value.publications) || !record(value.by_view) || !Array.isArray(value.by_league_market)) return fail();
  for (const r of value.publications) {
    if (!record(r) || !hash(r.publication_id) || !timestamp(r.published_at_utc) || !timestamp(r.forecast_created_at_utc)
      || !record(r.offer) || typeof r.offer.opportunity_id !== "string" || !/^[0-9a-f]{32}$/.test(r.offer.opportunity_id)
      || !Array.isArray(r.ranking_views)) return fail();
    const { opportunity_id: _, ...offer } = r.offer;
    try { decodeRecommendations([offer]); } catch { return fail(); }
    const o = offer as unknown as Recommendation;
    const views = ["best_value", ...(o.model_probability >= .65 ? ["higher_probability"] : [])];
    if (JSON.stringify(r.ranking_views) !== JSON.stringify(views) || o.american_odds < -200 || o.no_vig_probability_edge < .05-1e-12
      || Date.parse(r.forecast_created_at_utc as string) > Date.parse(r.published_at_utc as string)
      || Date.parse(o.availability_checked_at_utc) > Date.parse(r.published_at_utc as string)
      || Date.parse(r.published_at_utc as string) >= Date.parse(o.kickoff_utc)) return fail();
    const win = o.american_odds > 0 ? o.american_odds/100 : 100/Math.abs(o.american_odds);
    if (r.result === "PENDING" ? r.profit_units !== null :
      !["WIN", "LOSS", "PUSH"].includes(r.result as string) || !finite(r.profit_units)
      || !close(r.profit_units as number, r.result === "WIN" ? win : r.result === "LOSS" ? -1 : 0)) return fail();
  }
  const report = value as unknown as TrackRecord;
  if (new Set(report.publications.map(r => r.publication_id)).size !== report.publications.length
    || new Set(report.publications.map(r => JSON.stringify([r.offer.competition, r.offer.provider, r.offer.provider_fixture_id,
      Date.parse(r.offer.kickoff_utc), r.offer.market_type, r.offer.team_side]))).size !== report.publications.length
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
