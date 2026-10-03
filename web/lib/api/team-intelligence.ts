import { ModelFCApiError } from "./errors";

// The backend owns registry membership. This bounds route/response syntax only.
export const isTeamId = (v: unknown): v is string => typeof v === "string" && v.length <= 32 && /^[a-z]{1,16}(?:-[a-z]{1,16})?$/.test(v);
export type CoverageState = "AVAILABLE" | "INSUFFICIENT_SAMPLE" | "INCOMPLETE_COVERAGE";
export type TeamIdentity = { team_id: string; display_name: string; source_name: string };
export type Sample = { completed: number; n: number; start_date: string | null; end_date: string | null; won_sum: number; conceded_sum: number; won: number | null; conceded: number | null; differential: number | null; total: number | null };
export type Window = { state: CoverageState; requested: 5 | 10; sample: Sample };
export type Recency = { last_five: Window; previous_five: Window; last_ten: Window; trend_state: CoverageState; won_change: number | null; conceded_change: number | null; differential_change: number | null; total_change: number | null };
export type Rank = { rank: number | null; cohort_size: number };
export type Threshold = { threshold: 9 | 10 | 11; count: number; denominator: number; frequency: number | null };
export type TeamSummary = { team: TeamIdentity; coverage: { completed: number; covered: number; missing: number; home: number; away: number; latest_result_date: string; latest_corner_date: string | null }; primary: Sample; home: Sample; away: Sample; recency: Recency; ranks: { won: Rank; conceded: Rank; differential: Rank; total: Rank }; thresholds: Threshold[] };
export const families = ["THRESHOLD_STREAK", "ATTACK_INCREASE", "ATTACK_DECLINE", "CONCESSION_INCREASE", "CONCESSION_DECLINE", "DIFFERENTIAL_CHANGE", "HOME_AWAY_SPLIT", "HIGH_MATCH_CORNER_ENVIRONMENT", "LOW_MATCH_CORNER_ENVIRONMENT"] as const;
export type Insight = { rule_version: "corner-patterns-v1"; family: typeof families[number]; team: TeamIdentity; scope: "ALL" | "HOME" | "AWAY"; metric: "won" | "conceded" | "differential" | "total"; recent: Sample; baseline: Sample | null; difference: number | null; threshold: 9 | 10 | 11 | null; streak: number | null; threshold_runs: number[] };
export type TeamMetadata = { schema_version: 1; calculation_version: "team-corners-v1"; competition: "E1"; season: string; data_cutoff: string | null; source_revision: string; source: "football-data"; roster_state: "COMPLETE" | "PARTIAL" };
export type TeamOverview = Pick<TeamSummary,"team"|"coverage"|"primary"|"ranks"> & { recency: Pick<Recency,"trend_state"|"won_change"> };
export type TeamList = { metadata: TeamMetadata; teams: TeamOverview[]; insights: Insight[] };
export type RecentMatch = { date: string; opponent: TeamIdentity; venue: "HOME" | "AWAY"; won: number | null; conceded: number | null; total: number | null };
export type TeamProfile = { metadata: TeamMetadata; summary: TeamSummary; recent_matches: RecentMatch[]; insights: Insight[] };
export type InsightList = { metadata: TeamMetadata; insights: Insight[] };

type Obj = Record<string, unknown>;
const obj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const keys = (v: unknown, names: string): v is Obj => obj(v) && Object.keys(v).sort().join(",") === names.split(" ").sort().join(",");
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const integer = (v: unknown, max = 46): v is number => num(v) && Number.isSafeInteger(v) && v >= 0 && v <= max;
const date = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) && Number.isFinite(Date.parse(v + "T00:00:00Z")) && new Date(v + "T00:00:00Z").toISOString().slice(0,10) === v;
const near = (a: unknown, b: number) => num(a) && Math.abs(a-b) < 1e-9;
const state = (v: unknown) => ["AVAILABLE", "INSUFFICIENT_SAMPLE", "INCOMPLETE_COVERAGE"].includes(v as string);
const identity = (v: unknown): v is TeamIdentity => keys(v, "team_id display_name source_name") && isTeamId(v.team_id) && [v.display_name, v.source_name].every(x => typeof x === "string" && x.trim().length > 0 && x.length <= 64);
const sample = (v: unknown): v is Sample => {
  if (!keys(v, "completed n start_date end_date won_sum conceded_sum won conceded differential total") || !integer(v.completed) || !integer(v.n) || v.n > v.completed || !integer(v.won_sum, 460000) || !integer(v.conceded_sum, 460000)) return false;
  if (v.completed === 0 ? v.start_date !== null || v.end_date !== null : !date(v.start_date) || !date(v.end_date) || v.start_date > v.end_date) return false;
  return v.n === 0 ? v.won_sum === 0 && v.conceded_sum === 0 && [v.won,v.conceded,v.differential,v.total].every(x => x === null) : near(v.won, v.won_sum/v.n) && near(v.conceded, v.conceded_sum/v.n) && near(v.differential, (v.won_sum-v.conceded_sum)/v.n) && near(v.total, (v.won_sum+v.conceded_sum)/v.n);
};
const window = (v: unknown): v is Window => keys(v, "state requested sample") && state(v.state) && [5,10].includes(v.requested as number) && sample(v.sample) && v.sample.completed <= (v.requested as number) && (v.state === "AVAILABLE" ? v.sample.n === v.requested : v.state === "INCOMPLETE_COVERAGE" ? v.sample.completed === v.requested && v.sample.n < (v.requested as number) : v.sample.completed < (v.requested as number));
const recency = (v: unknown): v is Recency => {
  if (!keys(v, "last_five previous_five last_ten trend_state won_change conceded_change differential_change total_change") || !window(v.last_five) || !window(v.previous_five) || !window(v.last_ten) || v.last_five.requested !== 5 || v.previous_five.requested !== 5 || v.last_ten.requested !== 10 || !state(v.trend_state)) return false;
  const available = v.last_five.state === "AVAILABLE" && v.previous_five.state === "AVAILABLE";
  if ((v.trend_state === "AVAILABLE") !== available) return false;
  const recent=v.last_five.sample, prior=v.previous_five.sample;
  return (["won", "conceded", "differential", "total"] as const).every(m => available ? near(v[`${m}_change`], recent[m]! - prior[m]!) : v[`${m}_change`] === null);
};
const rank = (v: unknown): v is Rank => keys(v,"rank cohort_size") && integer(v.cohort_size,24) && (v.rank === null || integer(v.rank,24) && v.rank >= 1 && v.rank <= v.cohort_size);
const summary = (v: unknown): v is TeamSummary => {
  if (!keys(v,"team coverage primary home away recency ranks thresholds") || !identity(v.team) || !sample(v.primary) || !sample(v.home) || !sample(v.away) || !recency(v.recency) || !keys(v.ranks,"won conceded differential total") || !Object.values(v.ranks).every(rank)) return false;
  const c=v.coverage, p=v.primary;
  if (!keys(c,"completed covered missing home away latest_result_date latest_corner_date") || ![c.completed,c.covered,c.missing].every(x=>integer(x)) || !integer(c.home,23) || !integer(c.away,23) || c.completed !== p.completed || c.covered !== p.n || (c.covered as number)+(c.missing as number)!==c.completed || (c.home as number)+(c.away as number)!==c.completed || !date(c.latest_result_date) || (c.latest_corner_date!==null && !date(c.latest_corner_date))) return false;
  if (v.home.completed !== c.home || v.away.completed !== c.away || v.home.n+v.away.n !== p.n || v.home.won_sum+v.away.won_sum!==p.won_sum || v.home.conceded_sum+v.away.conceded_sum!==p.conceded_sum) return false;
  return Array.isArray(v.thresholds) && v.thresholds.length===3 && v.thresholds.every((t,i)=>keys(t,"threshold count denominator frequency") && t.threshold===9+i && integer(t.count) && t.denominator===p.n && (t.count as number)<=p.n && (p.n===0 ? t.frequency===null : near(t.frequency,(t.count as number)/p.n))) && v.thresholds.every((t,i,a)=>!i || t.count<=a[i-1].count);
};
const metadata = (v: unknown): v is TeamMetadata => keys(v,"schema_version calculation_version competition season data_cutoff source_revision source roster_state") && v.schema_version===1 && v.calculation_version==="team-corners-v1" && v.competition==="E1" && typeof v.season==="string" && /^\d{4}$/.test(v.season) && (v.data_cutoff===null || date(v.data_cutoff)) && typeof v.source_revision==="string" && /^[0-9a-f]{64}$/.test(v.source_revision) && v.source==="football-data" && ["COMPLETE","PARTIAL"].includes(v.roster_state as string);
const insight = (v: unknown): v is Insight => {
  if (!keys(v,"rule_version family team scope metric recent baseline difference threshold streak threshold_runs") || v.rule_version!=="corner-patterns-v1" || !families.includes(v.family as Insight["family"]) || !identity(v.team) || !["ALL","HOME","AWAY"].includes(v.scope as string) || !["won","conceded","differential","total"].includes(v.metric as string) || !sample(v.recent) || (v.baseline!==null && !sample(v.baseline)) || (v.difference!==null && !num(v.difference)) || !Array.isArray(v.threshold_runs) || v.threshold_runs.length>3 || !v.threshold_runs.every(x=>integer(x))) return false;
  if (v.scope !== "ALL") return false;
  if (v.family==="THRESHOLD_STREAK") return v.metric === "total" && v.recent.completed === v.streak && [9,10,11].includes(v.threshold as number) && integer(v.streak) && v.streak>=5 && v.recent.n===v.streak && v.threshold_runs.length===3 && v.threshold_runs[(v.threshold as number)-9]===v.streak && v.baseline===null && v.difference===null;
  if (v.threshold!==null || v.streak!==null || v.threshold_runs.length!==0) return false;
  if (v.family==="HIGH_MATCH_CORNER_ENVIRONMENT" || v.family==="LOW_MATCH_CORNER_ENVIRONMENT") return v.recent.n>=5 && v.metric==="total" && v.baseline===null && v.difference===null && (v.family==="HIGH_MATCH_CORNER_ENVIRONMENT" ? v.recent.total!>=11 : v.recent.total!<=9);
  if (!sample(v.baseline) || !num(v.difference) || Math.abs(v.difference)<1 || !near(v.difference, v.recent[v.metric as keyof Pick<Sample,"won"|"conceded"|"differential"|"total">]! - v.baseline[v.metric as keyof Pick<Sample,"won"|"conceded"|"differential"|"total">]!)) return false;
  if (v.family === "HOME_AWAY_SPLIT") return v.metric === "won" && v.recent.n >= 5 && v.baseline.n >= 5;
  const family = v.family as Insight["family"];
  const expectedMetric = family.startsWith("ATTACK") ? "won" : family.startsWith("CONCESSION") ? "conceded" : "differential";
  if (v.metric !== expectedMetric || (family.endsWith("INCREASE") && v.difference <= 0) || (family.endsWith("DECLINE") && v.difference >= 0)) return false;
  return v.recent.n === 5 && v.baseline.n === 5 && v.recent.completed === 5 && v.baseline.completed === 5 && v.baseline.end_date! < v.recent.start_date!;
};
const insights = (v: unknown, max: number): v is Insight[] => Array.isArray(v) && v.length<=max && v.every(insight);
function fail(): never { throw new ModelFCApiError("The API returned team intelligence outside the V1 contract.","TEAM_INTELLIGENCE_CONTRACT_MISMATCH",false); }
const overview = (v: unknown): v is TeamOverview => {
  if (!keys(v,"team coverage primary ranks recency") || !identity(v.team) || !sample(v.primary) || !keys(v.ranks,"won conceded differential total") || !Object.values(v.ranks).every(rank) || !keys(v.recency,"trend_state won_change") || !state(v.recency.trend_state) || (v.recency.trend_state === "AVAILABLE" ? !num(v.recency.won_change) : v.recency.won_change!==null)) return false;
  const c=v.coverage, p=v.primary;
  return keys(c,"completed covered missing home away latest_result_date latest_corner_date") && integer(c.completed) && integer(c.covered) && integer(c.missing) && integer(c.home,23) && integer(c.away,23) && c.completed===p.completed && c.covered===p.n && c.covered+c.missing===c.completed && c.home+c.away===c.completed && date(c.latest_result_date) && (c.latest_corner_date===null || date(c.latest_corner_date));
};
export function decodeTeams(v: unknown): TeamList {
  if (!keys(v,"metadata teams insights") || !metadata(v.metadata) || !Array.isArray(v.teams) || v.teams.length>24 || !v.teams.every(overview) || !insights(v.insights,6)) fail();
  const teams=v.teams as TeamOverview[], findings=v.insights as Insight[];
  if (new Set(teams.map(t=>t.team.team_id)).size!==teams.length || new Set(findings.map(i=>i.team.team_id)).size!==findings.length || findings.some(i=>!teams.some(t=>t.team.team_id===i.team.team_id))) fail();
  return v as TeamList;
}
export function decodeTeamProfile(v: unknown, id: string): TeamProfile {
  if (!keys(v,"metadata summary recent_matches insights") || !metadata(v.metadata) || !summary(v.summary) || v.summary.team.team_id!==id || !insights(v.insights,3) || v.insights.some(i=>i.team.team_id!==id) || !Array.isArray(v.recent_matches) || v.recent_matches.length>10 || !v.recent_matches.every((m,i,a)=>keys(m,"date opponent venue won conceded total") && date(m.date) && identity(m.opponent) && m.opponent.team_id!==id && ["HOME","AWAY"].includes(m.venue as string) && (m.won===null ? m.conceded===null && m.total===null : integer(m.won,9999) && integer(m.conceded,9999) && m.total===m.won+m.conceded) && (!i || (a[i-1].date as string)>(m.date as string)))) fail();
  return v as TeamProfile;
}
export function decodeTeamInsights(v: unknown): InsightList {
  if (!keys(v,"metadata insights") || !metadata(v.metadata) || !insights(v.insights,6) || new Set(v.insights.map(i=>i.team.team_id)).size!==v.insights.length) fail();
  return v as InsightList;
}
