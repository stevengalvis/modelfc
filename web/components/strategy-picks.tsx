"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { StrategyPage, StrategyPick } from "@/lib/api/track-record";
import type { ShortlistRanking } from "@/lib/api/types";

export function StrategyPicks({ scope }: { scope: "current" | "history" }) {
  const [strategy, setStrategy] = useState<ShortlistRanking>("best_value");
  const [market, setMarket] = useState("");
  const [competition, setCompetition] = useState("");
  return <section className="panel ledger-panel" aria-label={scope === "current" ? "Model Picks" : "Strategy Results"}>
    <h2>{scope === "current" ? "Model Picks" : "Strategy Results"}</h2>
    <div role="tablist" aria-label="Pick strategy">
      {(["best_value", "higher_probability"] as const).map(view => <button role="tab" aria-selected={view===strategy} key={view} onClick={()=>setStrategy(view)}>{view === "best_value" ? "Best Value" : "Higher Probability"}</button>)}
    </div>
    <p>{strategy === "best_value" ? "Highest estimated EV among eligible selections." : "First restrict to at least 65% modeled win probability, then choose highest estimated EV."} One frozen choice per strategy and market. Odds at least −200, no-vig edge at least 5 percentage points, positive EV.</p>
    <p>Simulated flat $2 risked per pick, including favorites. No wagers are placed. Model probabilities are estimates, not proven hit rates.</p>
    <label>Market <select value={market} onChange={e=>setMarket(e.target.value)}><option value="">All markets</option><option value="TEAM_TOTAL">Team corners</option><option value="BTTS">Both teams to score</option></select></label>
    <label>League <select value={competition} onChange={e=>setCompetition(e.target.value)}><option value="">All supported leagues</option><option value="E1">Championship</option><option value="E0">Premier League</option><option value="SP1">La Liga</option></select></label>
    <StrategyRows key={`${scope}:${strategy}:${market}:${competition}`} scope={scope} strategy={strategy} market={market} competition={competition} />
  </section>;
}
function StrategyRows({scope,strategy,market,competition}: {scope:"current"|"history"; strategy:ShortlistRanking; market:string; competition:string}) {
  const [offset,setOffset]=useState(0), [attempt,setAttempt]=useState(0);
  const [data,setData]=useState<StrategyPage|null>(null), [error,setError]=useState(false);
  useEffect(()=>{
    const controller=new AbortController(); setData(null); setError(false);
    api.strategyPicks({scope,strategy,market,competition,offset},controller.signal)
      .then(value=>{if(!controller.signal.aborted)setData(value);})
      .catch(()=>{if(!controller.signal.aborted)setError(true);});
    return ()=>controller.abort();
  },[scope,strategy,market,competition,offset,attempt]);
  if(error)return <p role="alert">Strategy picks could not be loaded. No result is inferred. <button onClick={()=>setAttempt(n=>n+1)}>Retry strategy picks</button></p>;
  if(!data)return <p role="status">Loading strategy picks…</p>;
  return <>
    <p>{data.total} picks match these filters. Showing {data.publications.length}.</p>
    {scope === "history" && <>
      <h3>Results for these filters</h3><p>{data.totals.settled} settled · {data.totals.pending} pending · {data.totals.wins} wins · {data.totals.losses} losses · {data.totals.pushes} pushes</p>
      <p>Simulated profit: {data.totals.profit_units.toFixed(2)} units / ${data.total_profit_dollars.toFixed(2)}. ROI: {data.totals.roi === null ? "—" : `${(data.totals.roi*100).toFixed(1)}%`}.</p>
      <p>Includes all published picks matching these filters, across pages. Pushes return the stake; pending picks are excluded from settled ROI. Sportsbook voids and BTTS result corrections remain unavailable.</p>
      {data.market_totals.map(g=><p key={`${g.competition}:${g.market_type}`}>{g.competition} · {g.market_type === "BTTS" ? "Both teams to score" : "Team corners"}: {g.summary.picks} picks, {g.summary.settled} settled</p>)}
    </>}
    {!data.publications.length && <p>No published picks for this strategy and filter yet. Historical subset picks are kept separately and are not reclassified.</p>}
    {data.publications.map(row=><article className="panel" key={row.publication_id}>
      <h3>{row.offer.home_team} vs {row.offer.away_team} · {row.offer.competition}</h3>
      <PublishedPick row={row} now={Date.now()} />
      <details><summary>Evidence and simulated result</summary><p>Published {row.published_at_utc}; policy effective {row.policy_effective_at_utc}. Strategy version: {row.strategy_policy}.</p>
        <p>No-vig edge {(row.offer.no_vig_probability_edge*100).toFixed(1)} pp · estimated EV {(row.offer.expected_profit*100).toFixed(1)}%.</p>
        <p>{row.result} · simulated $2 risked · {row.profit_units === null ? "Pending" : `${row.profit_units.toFixed(2)} units / $${row.profit_dollars?.toFixed(2)}`}</p></details>
    </article>)}
    <button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-5))}>Previous picks</button>
    <button disabled={data.next_offset===null} onClick={()=>data.next_offset!==null&&setOffset(data.next_offset)}>Next picks</button>
  </>;
}

/** Shared immutable publication evidence; newer quotes never replace these values. */
export function PublishedPick({ row, now }: { row: StrategyPick; now: number }) {
  return <div>
    <p>{row.offer.market_type === "BTTS" ? `Both teams to score: ${row.offer.side === "YES" ? "Yes" : "No"}` : `${row.offer.team} corners ${row.offer.direction.toLowerCase()} ${row.offer.line}`}</p>
    <p>{row.offer.bookmaker} · {row.offer.american_odds > 0 ? "+" : ""}{row.offer.american_odds} · {(row.offer.model_probability*100).toFixed(1)}% model probability</p>
    <p className="ledger-meta">Frozen publication price · Quote recorded <time dateTime={row.offer.retrieved_at_utc}>{row.offer.retrieved_at_utc}</time> · Published <time dateTime={row.published_at_utc}>{row.published_at_utc}</time></p>
    <p className="ledger-meta">{now >= Date.parse(row.offer.kickoff_utc) ? "Started fixture · publication is historical, not an upcoming choice." : now - Date.parse(row.offer.retrieved_at_utc) > 300_000 ? "Stale quote · price needs rechecking." : "Saved quote · recheck price and availability."}</p>
  </div>;
}
