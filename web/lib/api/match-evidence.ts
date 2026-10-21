import type { StrategyPick } from './track-record';

type Sample = { completed: number; n: number; start_date: string | null; end_date: string | null; won: number | null; conceded: number | null };
type Team = { team: string; state: string; venue: string; small_sample: boolean; missing_corner_rows: number; season: Sample; venue_sample: Sample };
type Value = { side: string; american_odds: number; decimal_odds: number; model_probability: number };
type Comparison = { bookmaker: string; observation_timestamp_utc: string; current_status: string; frozen_at_utc: string; model_name: string; model_version: string; history_cutoff_date: string; yes: Value; no: Value };
export type MatchEvidence = { schema_version: 1; state: 'UNSUPPORTED' } | {
  schema_version: 1; state: 'PARTIAL'; as_of_utc: string;
  fixture: { competition: string; home_team: string; away_team: string; kickoff_utc: string };
  publication: { published_at_utc: string; forecast_created_at_utc: string; quote_state: string; offer: Record<string, string | number | null> };
  corner_snapshot_state: 'NOT_BOUND_TO_PUBLICATION';
  team_corners: { state: string; source_freshness: 'UNKNOWN'; cutoff_date: string | null; teams: Team[] };
  saved_btts: { state: string; omitted: number; comparisons: Comparison[] };
};
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown) => typeof v === 'string' && v.length <= 300;
const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
const date = (v: unknown) => text(v) && Number.isFinite(Date.parse(v as string));
const nullable = (v: unknown, check: (v: unknown) => boolean) => v === null || check(v);
const keys = (v: Record<string, unknown>, names: string[]) => Object.keys(v).every(k => names.includes(k)) && names.every(k => k in v);
const sample = (v: unknown) => object(v) && keys(v, ['completed','n','start_date','end_date','won','conceded']) && num(v.completed) && num(v.n) && nullable(v.start_date,date) && nullable(v.end_date,date) && nullable(v.won,num) && nullable(v.conceded,num);
const value = (v: unknown) => object(v) && keys(v,['side','american_odds','decimal_odds','model_probability']) && ['YES','NO'].includes(v.side as string) && num(v.american_odds) && num(v.decimal_odds) && num(v.model_probability);
export function decodeMatchEvidence(raw: unknown, pick: StrategyPick): MatchEvidence {
  const fail = (): never => { throw new Error('MATCH_EVIDENCE_CONTRACT_MISMATCH'); };
  if (!object(raw) || raw.schema_version !== 1) return fail();
  if (raw.state === 'UNSUPPORTED' && keys(raw,['schema_version','state'])) return raw as MatchEvidence;
  if (raw.state !== 'PARTIAL' || !keys(raw,['schema_version','state','as_of_utc','fixture','publication','corner_snapshot_state','team_corners','saved_btts']) || !date(raw.as_of_utc)) return fail();
  const f = raw.fixture, p = raw.publication, t = raw.team_corners, b = raw.saved_btts;
  if (!object(f) || !keys(f,['competition','home_team','away_team','kickoff_utc']) || Object.entries(f).some(([k,v]) => v !== pick.offer[k as keyof typeof pick.offer])) return fail();
  if (!object(p) || !keys(p,['published_at_utc','forecast_created_at_utc','quote_state','offer']) || p.published_at_utc !== pick.published_at_utc || !date(p.forecast_created_at_utc) || !['STALE','FUTURE','RECENT_RECORDED'].includes(p.quote_state as string) || !object(p.offer)) return fail();
  const allowed = ['market_type','team','team_side','direction','line','side','bookmaker','american_odds','decimal_odds','retrieved_at_utc','model_probability','push_probability','expected_profit','no_vig_probability_edge'];
  const expected = allowed.filter(k => k in pick.offer);
  if (!keys(p.offer, expected) || Object.entries(p.offer).some(([k,v]) => v !== pick.offer[k as keyof typeof pick.offer])) return fail();
  if (raw.corner_snapshot_state !== 'NOT_BOUND_TO_PUBLICATION' || !object(t) || !keys(t,['state','source_freshness','cutoff_date','teams']) || !text(t.state) || t.source_freshness !== 'UNKNOWN' || !nullable(t.cutoff_date,date) || !Array.isArray(t.teams) || t.teams.length > 2 || !t.teams.every(v => object(v) && keys(v,['team','state','venue','small_sample','missing_corner_rows','season','venue_sample']) && text(v.state) && (v.venue === 'HOME' && v.team === f.home_team || v.venue === 'AWAY' && v.team === f.away_team) && typeof v.small_sample === 'boolean' && num(v.missing_corner_rows) && sample(v.season) && sample(v.venue_sample))) return fail();
  if (!object(b) || !keys(b,['state','omitted','comparisons']) || !text(b.state) || !num(b.omitted) || !Array.isArray(b.comparisons) || b.comparisons.length > 2 || !b.comparisons.every(v => object(v) && keys(v,['bookmaker','observation_timestamp_utc','current_status','frozen_at_utc','model_name','model_version','history_cutoff_date','yes','no']) && ['bookmaker','current_status','model_name','model_version'].every(k => text(v[k])) && ['observation_timestamp_utc','frozen_at_utc','history_cutoff_date'].every(k=>date(v[k])) && value(v.yes) && value(v.no))) return fail();
  return raw as MatchEvidence;
}
