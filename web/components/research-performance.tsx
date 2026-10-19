"use client";
import { useEffect, useState } from 'react';
import { api } from '@/lib/api/client';
import { defaultResearchFilters, type ResearchFilters, type ResearchPerformance } from '@/lib/api/research-performance';
import { PublicationCutoff } from './publication-cutoff';
const metric=(n:number|null)=>n===null?'Unavailable':n.toFixed(4);
export function ResearchQualityDashboard() {
 const [filters,setFilters]=useState<ResearchFilters>(defaultResearchFilters);
 const [data,setData]=useState<ResearchPerformance|null>(null),[error,setError]=useState<string|null>(null);
 const [attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();setData(null);setError(null);
  api.researchPerformance(controller.signal,filters).then(value=>{if(!controller.signal.aborted)setData(value);})
   .catch((reason:unknown)=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Request failed.');});
  return ()=>controller.abort();
 },[filters,attempt]);
 return <section className="panel ledger-panel">
  <h2>Saved forecast quality & research history</h2>
  <p>One earliest frozen forecast per exact fixture and market within the selected cohort supplies quality metrics. All retained forecasts remain visible in chronological order. Priced publications and strategy returns have their own tabs.</p>
  <div className="track-filters">
   <label>Research league <select value={filters.competition} onChange={e=>setFilters({...filters,competition:e.target.value as ResearchFilters['competition']})}><option value="ALL">Overall · partial E1 coverage</option><option value="E1">Championship · E1</option><option value="E0">Premier League · unavailable</option></select></label>
   <label>Research market <select value={filters.market} onChange={e=>setFilters({...filters,market:e.target.value as ResearchFilters['market']})}><option value="ALL">All markets</option><option value="CORNER">Corners</option><option value="BTTS">BTTS</option></select></label>
   <label>Forecast cohort <select value={filters.cohort} onChange={e=>setFilters({...filters,cohort:e.target.value as ResearchFilters['cohort']})}><option value="ALL">All saved origins</option><option value="ODDS_FREE">Odds-free research</option><option value="BOOKMAKER_RESEARCH">Bookmaker-linked research</option></select></label>
  </div>
  <PublicationCutoff kind="Research" onApply={asOf=>setFilters({...filters,asOf})}/>
  {error?<div role="alert"><p>{error}</p><button onClick={()=>setAttempt(n=>n+1)}>Retry research report</button></div>:!data?<p role="status">Loading saved research evidence…</p>:<>
   <p>Coverage: {data.coverage}. {data.unique_fixtures} unique fixtures · {data.forecast_cohorts} forecast cohorts. Effective cutoff: {data.selection.as_of_utc}.</p>
   <ul>{data.limitations.map(text=><li key={text}>{text}</li>)}</ul>
   {data.markets.map(m=><section key={m.market} aria-label={`${m.market} quality`}>
    <h3>{m.market} quality</h3>
    <p>{m.unique_fixtures} primary fixtures · {m.forecast_cohorts} forecast cohorts · {m.settled_fixtures} settled · {m.unresolved_fixtures} unresolved · {m.upcoming_fixtures} upcoming</p>
    <dl className="performance-grid"><div><dt>Brier score</dt><dd>{metric(m.probability_scores.brier_score)}</dd></div><div><dt>Log loss</dt><dd>{metric(m.probability_scores.log_loss)}</dd></div>
     {m.market==='CORNER'&&<><div><dt>Team corner MAE</dt><dd>{metric(m.team_corner_mae)}</dd></div><div><dt>Team corner RMSE</dt><dd>{metric(m.team_corner_rmse)}</dd></div></>}
    </dl>
    <p>{m.probability_scores.sample_count} scored {m.market==='BTTS'?'fixture YES probabilities':'saved decisive team-total targets'} · {m.pushes_excluded} pushes excluded. {m.market==='CORNER'&&`${m.team_count_sample_count} team counts scored. Line targets are correlated, not independent fixture samples.`}</p>
    {m.probability_scores.sample_count<30&&<p>Small sample: these scores do not establish predictive reliability.</p>}
    {m.probability_scores.calibration.length>0&&<table><caption>{m.market} calibration</caption><thead><tr><th>Probability range</th><th>Mean predicted</th><th>Observed frequency</th><th>Samples</th></tr></thead><tbody>{m.probability_scores.calibration.map(b=><tr key={b.lower}><td>{b.lower}–{b.upper}</td><td>{metric(b.mean_probability)}</td><td>{metric(b.observed_frequency)}</td><td>{b.sample_count}</td></tr>)}</tbody></table>}
   </section>)}
   <h3>Retained forecast history</h3>
   {!data.forecasts.length?<p>No supported saved forecasts for this selection. Missing evidence is unavailable, not zero performance.</p>:data.forecasts.map(r=><article className="ledger-card" key={`${r.market}:${r.forecast_id}`}>
    <h4>{r.home_team} vs {r.away_team} · {r.market}</h4><p>{r.competition} · Kickoff {r.kickoff_utc} · {r.status} · {r.selected_for_metrics?'Primary scoring cohort':'Additional forecast cohort'}</p>
    <p>Frozen {r.frozen_at_utc} · {r.model_name} / {r.model_version} · {r.origins.join(', ')}</p>
    {r.market==='CORNER'&&<p>Expected corners: home {r.expected_home_corners}, away {r.expected_away_corners}.</p>}
    <p>Effective result: {r.result?JSON.stringify(r.result):'Pending / unavailable'}. Outcome recorded: {r.outcome_recorded_at_utc??'Unavailable'}.</p>
    <details><summary>Original probabilities, source references and outcome revisions</summary><p>Missing retrieval times remain null. Correction support: {r.correction_support}. IDs refer to immutable saved evidence.</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(r,null,2)}</pre></details>
   </article>)}
  </>}
 </section>;
}
