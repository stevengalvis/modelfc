import { ModelFCApiError } from './errors';
import { record, finite, timestamp } from './prospective';
export interface ResearchFilters { competition: 'ALL'|'E1'|'E0'; market: 'ALL'|'CORNER'|'BTTS'; cohort: 'ALL'|'ODDS_FREE'|'BOOKMAKER_RESEARCH'; asOf?: string }
export const defaultResearchFilters: ResearchFilters = { competition:'ALL', market:'ALL', cohort:'ALL' };
export interface ResearchForecast {
  fixture_id:string; forecast_id:string; source_forecast_id:string; competition:'E1'; market:'CORNER'|'BTTS';
  home_team:string; away_team:string; kickoff_utc:string; frozen_at_utc:string; model_name:string; model_version:string;
  origins:string[]; selected_for_metrics:boolean; status:'SETTLED'|'UPCOMING'|'UNRESOLVED';
  source_references:{kind:string;sha256:string;retrieved_at_utc:string|null}[];
  probabilities:{target_id:string;team_side:string|null;direction:string;line:number|null;probability:number;push_probability:number;decisive_probability:number;recorded_at_utc:string;result:string|null}[];
  expected_home_corners:number|null;expected_away_corners:number|null;result:Record<string,unknown>|null;
  outcome_id:string|null;outcome_recorded_at_utc:string|null;result_provider:string|null;result_source_sha256:string|null;
  effective_record_hash:string|null;corrections:{record_id:string;recorded_at_utc:string;supersedes_id:string|null;reason:string|null;source_sha256:string|null;result:Record<string,unknown>}[];
  correction_support:'SUPPORTED'|'UNSUPPORTED';
}
export interface ResearchSummary {
 market:'CORNER'|'BTTS';unique_fixtures:number;forecast_cohorts:number;settled_fixtures:number;unresolved_fixtures:number;upcoming_fixtures:number;
 probability_unit:string;saved_probability_targets:number;pushes_excluded:number;
 probability_scores:{sample_count:number;brier_score:number|null;log_loss:number|null;calibration:{lower:number;upper:number;sample_count:number;mean_probability:number;observed_frequency:number}[]};
 team_count_sample_count:number;team_corner_mae:number|null;team_corner_rmse:number|null;
}
export interface ResearchPerformance {
 selection:{competition:string;market:string;cohort:string;as_of_utc:string};policy:string;coverage:string;historical_store_membership:'UNVERIFIED';
 unique_fixtures:number;forecast_cohorts:number;markets:ResearchSummary[];limitations:string[];forecasts:ResearchForecast[];
}
const count=(v:unknown)=>Number.isSafeInteger(v)&&Number(v)>=0;
const prob=(v:unknown)=>finite(v)&&v>=0&&v<=1;
export function decodeResearchPerformance(value:unknown, filters:ResearchFilters):ResearchPerformance {
 const fail=():never=>{throw new ModelFCApiError('Saved research report does not match its evidence or requested filters.','RESEARCH_CONTRACT_MISMATCH',false);};
 if(!record(value)||!record(value.selection)||value.policy!=='earliest-frozen-per-exact-fixture-and-market-v1'
  ||value.historical_store_membership!=='UNVERIFIED'||!Array.isArray(value.forecasts)||!Array.isArray(value.markets)
  ||!Array.isArray(value.limitations)||!value.limitations.every(x=>typeof x==='string')
  ||value.selection.competition!==filters.competition||value.selection.market!==filters.market||value.selection.cohort!==filters.cohort
  ||!timestamp(value.selection.as_of_utc)||(filters.asOf&&Date.parse(String(value.selection.as_of_utc))!==Date.parse(filters.asOf))
  ||value.coverage!==(filters.competition==='E0'?'UNSUPPORTED':filters.competition==='ALL'?'PARTIAL':'SUPPORTED')) return fail();
 const cutoff=Date.parse(String(value.selection.as_of_utc)), seen=new Set<string>(); let last=-Infinity;
 for(const r of value.forecasts){
  if(!record(r)||!['CORNER','BTTS'].includes(String(r.market))||r.competition!=='E1'||filters.competition==='E0'
   ||(filters.market!=='ALL'&&r.market!==filters.market)||!Array.isArray(r.origins)||!r.origins.length
   ||!r.origins.every(x=>['ODDS_FREE','BOOKMAKER_RESEARCH'].includes(x))||(filters.cohort!=='ALL'&&!r.origins.includes(filters.cohort))
   ||!['fixture_id','forecast_id','source_forecast_id','home_team','away_team','model_name','model_version'].every(k=>typeof r[k]==='string'&&r[k])
   ||!timestamp(r.frozen_at_utc)||!timestamp(r.kickoff_utc)||Date.parse(String(r.frozen_at_utc))>cutoff||Date.parse(String(r.frozen_at_utc))<last
   ||!['SETTLED','UPCOMING','UNRESOLVED'].includes(String(r.status))
   ||!Array.isArray(r.probabilities)||!Array.isArray(r.corrections)||!Array.isArray(r.source_references))return fail();
  last=Date.parse(String(r.frozen_at_utc)); const key=`${r.market}:${r.fixture_id}`;
  if(r.selected_for_metrics!==!seen.has(key))return fail(); seen.add(key);
  if(r.status==='SETTLED' ? !record(r.result)||!timestamp(r.outcome_recorded_at_utc)||Date.parse(String(r.outcome_recorded_at_utc))>cutoff : r.result!==null||r.outcome_id!==null)return fail();
  for(const p of r.probabilities)if(!record(p)||!prob(p.probability)||!prob(p.push_probability)||!prob(p.decisive_probability)
   ||!timestamp(p.recorded_at_utc)||Date.parse(String(p.recorded_at_utc))>cutoff||![null,'WIN','LOSS','PUSH'].includes(p.result as null|string))return fail();
  for(const c of r.corrections)if(!record(c)||!timestamp(c.recorded_at_utc)||Date.parse(String(c.recorded_at_utc))>cutoff||!record(c.result))return fail();
  for(const s of r.source_references)if(!record(s)||typeof s.kind!=='string'||typeof s.sha256!=='string'||!(s.retrieved_at_utc===null||timestamp(s.retrieved_at_utc)))return fail();
 }
 const report=value as unknown as ResearchPerformance;
 if(report.forecast_cohorts!==report.forecasts.length||report.unique_fixtures!==new Set(report.forecasts.map(r=>r.fixture_id)).size
  ||report.markets.map(m=>m.market).join(',')!==(filters.market==='ALL'?'CORNER,BTTS':filters.market))return fail();
 for(const m of report.markets){
  const rows=report.forecasts.filter(r=>r.market===m.market), primary=rows.filter(r=>r.selected_for_metrics);
  const targets=primary.flatMap(r=>r.probabilities).filter(p=>m.market!=='BTTS'||p.direction==='YES'), scored=targets.filter(p=>p.result==='WIN'||p.result==='LOSS');
  if(!record(m.probability_scores)||!Array.isArray(m.probability_scores.calibration)||m.forecast_cohorts!==rows.length||m.unique_fixtures!==primary.length
   ||m.settled_fixtures!==primary.filter(r=>r.status==='SETTLED').length||m.unresolved_fixtures!==primary.filter(r=>r.status==='UNRESOLVED').length
   ||m.upcoming_fixtures!==primary.filter(r=>r.status==='UPCOMING').length||m.saved_probability_targets!==targets.length||m.pushes_excluded!==targets.filter(p=>p.result==='PUSH').length
   ||m.probability_scores.sample_count!==scored.length||!count(m.team_count_sample_count)
   ||(scored.length ? !prob(m.probability_scores.brier_score)||!finite(m.probability_scores.log_loss)||m.probability_scores.log_loss<0 : m.probability_scores.brier_score!==null||m.probability_scores.log_loss!==null)
   ||![m.team_corner_mae,m.team_corner_rmse].every(x=>x===null||(finite(x)&&x>=0)))return fail();
 }
 return report;
}
