import { ModelFCApiError } from "./errors";
export interface BttsPerformance {
  competition: "E1"; research_only: true; selection_policy: string; log_loss_epsilon: number;
  unique_fixtures: number; forecast_cohorts: number; settled_fixtures: number;
  unresolved_fixtures: number; upcoming_fixtures: number;
  metrics: { sample_count: number; brier_score: number | null; log_loss: number | null;
    calibration: { lower: number; upper: number; sample_count: number; mean_probability: number; observed_frequency: number }[] };
  forecasts: { fixture_id: string; forecast_id: string; competition: "E1"; home_team: string; away_team: string;
    kickoff_utc: string; provider: string; provider_fixture_id: string; origins: string[];
    selected_for_metrics: boolean; frozen_at_utc: string; model_version: string;
    yes_probability: number; no_probability: number; source_data_hashes: { competition: string; filename: string; sha256: string }[];
    status: "SETTLED" | "UPCOMING" | "UNRESOLVED"; actual_btts: "YES" | "NO" | null;
    home_goals: number | null; away_goals: number | null; outcome_id: string | null;
    effective_record_hash?: string | null;
    corrections?: { correction_id: string; supersedes_record_hash: string; record_hash: string; reason: string; recorded_at_utc: string; result: { home_goals: number; away_goals: number } }[];
    recorded_at_utc: string | null; result_provider: string | null; result_source_sha256: string | null }[];
}
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
const hash = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const count = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const probability = (v: unknown): v is number => finite(v) && v >= 0 && v <= 1;
const time = (v: unknown): v is string => text(v) && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const close = (a: number, b: number) => Math.abs(a-b) < 1e-9;

export function decodeBttsPerformance(value: unknown): BttsPerformance {
  const fail = (): never => { throw new ModelFCApiError("BTTS performance response is invalid.", "INVALID_RESPONSE", false, 502); };
  if (!object(value) || value.competition !== "E1" || value.research_only !== true
    || value.selection_policy !== "e1-earliest-frozen-per-exact-fixture-v1" || value.log_loss_epsilon !== 1e-15
    || ![value.unique_fixtures, value.forecast_cohorts, value.settled_fixtures, value.unresolved_fixtures, value.upcoming_fixtures].every(count)
    || !object(value.metrics) || !count(value.metrics.sample_count) || !Array.isArray(value.metrics.calibration)
    || !Array.isArray(value.forecasts)) return fail();
  for (const row of value.forecasts) {
    if (object(row) && row.corrections !== undefined && (!Array.isArray(row.corrections)
      || row.corrections.some(r => !object(r) || !hash(r.correction_id) || !hash(r.supersedes_record_hash)
        || !hash(r.record_hash) || !text(r.reason) || !time(r.recorded_at_utc) || !object(r.result)
        || !count(r.result.home_goals) || !count(r.result.away_goals)))) return fail();
    if (!object(row) || row.competition !== "E1" || !hash(row.fixture_id) || !hash(row.forecast_id)
      || ![row.home_team, row.away_team, row.provider, row.provider_fixture_id, row.model_version].every(text)
      || row.home_team === row.away_team || !time(row.kickoff_utc) || !time(row.frozen_at_utc)
      || Date.parse(row.frozen_at_utc) >= Date.parse(row.kickoff_utc)
      || !probability(row.yes_probability) || !probability(row.no_probability) || !close(row.yes_probability + row.no_probability, 1)
      || typeof row.selected_for_metrics !== "boolean" || !Array.isArray(row.origins) || !row.origins.length
      || new Set(row.origins).size !== row.origins.length || row.origins.some(v => !["BOOKMAKER_RESEARCH", "ODDS_FREE"].includes(v))
      || !Array.isArray(row.source_data_hashes) || !row.source_data_hashes.length
      || row.source_data_hashes.some(s => !object(s) || s.competition !== "E1" || !text(s.filename) || !hash(s.sha256))) return fail();
    if (row.status === "SETTLED") {
      if (!count(row.home_goals) || !count(row.away_goals)
        || row.actual_btts !== (row.home_goals > 0 && row.away_goals > 0 ? "YES" : "NO")
        || !hash(row.outcome_id) || row.outcome_id !== row.fixture_id || !hash(row.result_source_sha256)
        || !time(row.recorded_at_utc) || Date.parse(row.recorded_at_utc) <= Date.parse(row.kickoff_utc)
        || !["football-data.co.uk", "sportmonks"].includes(row.result_provider as string)) return fail();
    } else if (!["UPCOMING", "UNRESOLVED"].includes(row.status as string)
      || [row.home_goals, row.away_goals, row.actual_btts, row.outcome_id, row.recorded_at_utc, row.result_provider, row.result_source_sha256].some(v => v !== null)) return fail();
  }
  const report = value as unknown as BttsPerformance;
  const fixtures = new Map<string, BttsPerformance["forecasts"][number]>(); const cohorts = new Set<string>();
  const ordered = [...report.forecasts].sort((a,b) => Date.parse(a.frozen_at_utc)-Date.parse(b.frozen_at_utc) || a.forecast_id.localeCompare(b.forecast_id));
  for (const row of ordered) {
    if (cohorts.has(row.forecast_id) || row.selected_for_metrics !== !fixtures.has(row.fixture_id)) return fail();
    const other = fixtures.get(row.fixture_id) ?? row;
    if (["home_team", "away_team", "kickoff_utc", "actual_btts", "home_goals", "away_goals", "outcome_id", "status"].some(k => row[k as keyof typeof row] !== other[k as keyof typeof row])) return fail();
    cohorts.add(row.forecast_id); fixtures.set(row.fixture_id, row);
  }
  const primary = report.forecasts.filter(r => r.selected_for_metrics);
  const settled = primary.filter(r => r.status === "SETTLED");
  if (report.unique_fixtures !== primary.length || report.forecast_cohorts !== report.forecasts.length
    || report.settled_fixtures !== settled.length || report.metrics.sample_count !== settled.length
    || report.unresolved_fixtures !== primary.filter(r => r.status === "UNRESOLVED").length
    || report.upcoming_fixtures !== primary.filter(r => r.status === "UPCOMING").length) return fail();
  const m = report.metrics;
  if (!settled.length) {
    if (m.brier_score !== null || m.log_loss !== null || m.calibration.length) return fail();
  } else {
    const pairs = settled.map(r => [r.yes_probability, r.actual_btts === "YES" ? 1 : 0]);
    const brier = pairs.reduce((sum,[p,y]) => sum+(p-y)**2,0)/pairs.length;
    const loss = -pairs.reduce((sum,[p,y]) => sum+Math.log(Math.max(1e-15,Math.min(1-1e-15,y ? p : 1-p))),0)/pairs.length;
    if (!probability(m.brier_score) || !finite(m.log_loss) || !close(m.brier_score,brier) || !close(m.log_loss,loss)) return fail();
    const expected = [0,0.2,0.4,0.6,0.8].map(lower => ({lower, upper: Math.round((lower+0.2)*10)/10,
      rows: pairs.filter(([p]) => p >= lower && (p < Math.round((lower+0.2)*10)/10 || lower === 0.8))})).filter(b => b.rows.length);
    if (m.calibration.length !== expected.length) return fail();
    expected.forEach((b,i) => {
      const bin = m.calibration[i];
      if (!object(bin) || bin.lower !== b.lower || bin.upper !== b.upper || bin.sample_count !== b.rows.length
        || !probability(bin.mean_probability) || !probability(bin.observed_frequency)
        || !close(bin.mean_probability,b.rows.reduce((s,[p]) => s+p,0)/b.rows.length)
        || !close(bin.observed_frequency,b.rows.reduce((s,[,y]) => s+y,0)/b.rows.length)) fail();
    });
  }
  return report;
}
export const emptyBttsPerformance: BttsPerformance = {
  competition: "E1", research_only: true, selection_policy: "e1-earliest-frozen-per-exact-fixture-v1", log_loss_epsilon: 1e-15,
  unique_fixtures: 0, forecast_cohorts: 0, settled_fixtures: 0, unresolved_fixtures: 0, upcoming_fixtures: 0,
  metrics: { sample_count: 0, brier_score: null, log_loss: null, calibration: [] }, forecasts: [],
};
