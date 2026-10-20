"use client";

import { useEffect, useId, useRef, useState, type ReactNode, type Ref } from "react";
import { api } from "@/lib/api/client";
import type { StrategyPage, StrategyPick } from "@/lib/api/track-record";
import type { ShortlistRanking } from "@/lib/api/types";

const strategies = ["higher_probability", "best_value"] as const;
const strategyLabel = (value: ShortlistRanking) => value === "higher_probability" ? "Higher Probability" : "Best Value";
const utcFormatter = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
});
const utc = (value: string) => utcFormatter.format(new Date(value)) + " UTC";

function Information({ label, context, children }: { label: string; context?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className="pick-information">
    <button className="pick-info-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span aria-hidden="true">ⓘ</span> {label}{context && <span className="pick-sr-only"> for {context}</span>} <span aria-hidden="true">{open ? "−" : "+"}</span>
    </button>
    <div id={id} hidden={!open} className="pick-information-content">{children}</div>
  </div>;
}

export function RecommendationsDashboard() {
  const [strategy, setStrategy] = useState<ShortlistRanking>("higher_probability");
  const [market, setMarket] = useState("");
  const [competition, setCompetition] = useState("");
  const id = useId();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const queryKey = `${strategy}:${market}:${competition}`;
  return <div className="workspace recommendations-workspace">
    <header className="page-heading"><h1>Recommendations</h1><p>Published picks at recorded prices.</p></header>
    <section className="panel recommendations-panel" aria-label="Model Picks">
      <h2>Model Picks</h2>
      <div className="pick-tabs" role="tablist" aria-label="Pick strategy">
        {strategies.map((view, index) => <button key={view} ref={node => { tabs.current[index] = node; }}
          id={`${id}-${view}`} role="tab" aria-selected={strategy === view} aria-controls={`${id}-panel`}
          tabIndex={strategy === view ? 0 : -1} onClick={() => setStrategy(view)} onKeyDown={event => {
            const next = event.key === "Home" ? 0 : event.key === "End" ? 1
              : event.key === "ArrowRight" || event.key === "ArrowLeft" ? 1 - index : null;
            if (next !== null) { event.preventDefault(); setStrategy(strategies[next]); tabs.current[next]?.focus(); }
          }}>{strategyLabel(view)}</button>)}
      </div>
      <div className="pick-filters">
        <label>Market <select value={market} onChange={event => setMarket(event.target.value)}>
          <option value="">All markets</option><option value="TEAM_TOTAL">Team corners</option><option value="BTTS">Both teams to score</option>
        </select></label>
        <label>League <select value={competition} onChange={event => setCompetition(event.target.value)}>
          <option value="">All supported leagues</option><option value="E1">Championship</option><option value="E0">Premier League</option><option value="SP1">La Liga</option>
        </select></label>
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${strategy}`} tabIndex={0}>
        <Information key={`help:${queryKey}`} label="How these picks are selected">
          <p>{strategy === "best_value" ? "Highest estimated EV among eligible selections." : "First restrict to at least 65% modeled win probability, then choose highest estimated EV."} One frozen choice per strategy and market. Odds at least −200, no-vig edge at least 5 percentage points, positive EV.</p>
          <p>Simulated flat $2 risked per pick, including favorites. No wagers are placed. Model probabilities are estimates, not proven hit rates.</p>
          <p>The list is fixed at its initial read time while loading more picks. Reload the page for newly published picks. Price and kickoff warnings continue to update.</p>
        </Information>
        {competition && competition !== "E1" && <p>No {competition} production coverage is established by this filter. Missing records are not zero model performance.</p>}
        <CurrentPicks key={queryKey} strategy={strategy} market={market} competition={competition} />
      </div>
    </section>
  </div>;
}

function CurrentPicks({ strategy, market, competition }: { strategy: ShortlistRanking; market: string; competition: string }) {
  const [request, setRequest] = useState(() => ({ offset: 0, attempt: 0, asOf: new Date(Date.now()).toISOString() }));
  const [snapshot, setSnapshot] = useState<{ page: StrategyPage; rows: StrategyPick[] } | null>(null);
  const snapshotRef = useRef(snapshot);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [changed, setChanged] = useState(false);
  const [now, setNow] = useState(Date.now);
  const firstNewCard = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const advance = () => setNow(Date.now());
    const timer = window.setInterval(advance, 1000);
    window.addEventListener("focus", advance);
    document.addEventListener("visibilitychange", advance);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", advance); document.removeEventListener("visibilitychange", advance); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setChanged(false);
    api.strategyPicks({ scope: "current", strategy, market, competition, offset: request.offset, asOf: request.asOf }, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        setError(false);
        const previous = snapshotRef.current;
        // Keep the existing publication-time cutoff across batches; fail closed if the retained store changes.
        if (request.offset && previous && (previous.page.total !== page.total
          || page.publications.some(row => previous.rows.some(old => old.publication_id === row.publication_id)))) {
          setChanged(true);
        } else {
          const next = { page, rows: request.offset && previous ? [...previous.rows, ...page.publications] : page.publications };
          snapshotRef.current = next;
          setSnapshot(next);
        }
        setLoading(false);
      }).catch(() => { if (!controller.signal.aborted) { setError(true); setLoading(false); } });
    return () => controller.abort();
  }, [strategy, market, competition, request]);
  useEffect(() => {
    if (request.offset && snapshot?.page.offset === request.offset) firstNewCard.current?.focus();
  }, [snapshot, request.offset]);
  const load = (offset: number) => { setLoading(true); setRequest(old => ({ offset, attempt: old.attempt + 1, asOf: offset === 0 ? new Date(Date.now()).toISOString() : old.asOf })); };
  return <>
    <p role="status" aria-atomic="true" className="pick-count">{loading ? snapshot ? "Loading more picks…" : "Loading strategy picks…"
      : snapshot ? `${snapshot.page.total} picks match these filters. Showing ${snapshot.rows.length}.${snapshot.rows.length && snapshot.page.next_offset === null ? " All matching picks shown." : ""}` : ""}</p>
    {snapshot && !snapshot.rows.length && <p>No published picks for this strategy and filter yet. Historical subset picks are kept separately and are not reclassified.</p>}
    <div className="pick-list">{snapshot?.rows.map((row, index) => <PickCard key={row.publication_id} row={row} now={now}
      cardRef={index === request.offset ? firstNewCard : undefined} />)}</div>
    {error && <p role="alert">Strategy picks could not be loaded. {snapshot ? "Previously loaded picks are retained." : "No result is inferred."} <button className="secondary-button" onClick={() => load(request.offset)} disabled={loading}>Retry strategy picks</button></p>}
    {changed && <p role="alert">The published list changed while loading. Reload picks to restore the current order. <button className="secondary-button" onClick={() => { setSnapshot(null); load(0); }}>Reload picks</button></p>}
    {snapshot && snapshot.page.next_offset !== null && !error && !changed && <button className="secondary-button pick-more" disabled={loading} onClick={() => load(snapshot.page.next_offset!)}>Show more picks</button>}
  </>;
}

function PickCard({ row, now, cardRef }: { row: StrategyPick; now: number; cardRef?: Ref<HTMLElement> }) {
  const offer = row.offer;
  const title = `${offer.home_team} vs ${offer.away_team}`;
  return <article className="panel pick-card" tabIndex={-1} ref={cardRef} aria-label={title}>
    <p className="pick-context">{offer.competition} · Kickoff <time dateTime={offer.kickoff_utc}>{utc(offer.kickoff_utc)}</time></p>
    <h3>{title}</h3>
    <p className="pick-selection">{offer.market_type === "BTTS" ? `Both teams to score: ${offer.side === "YES" ? "Yes" : "No"}` : `${offer.team} corners ${offer.direction.toLowerCase()} ${offer.line}`}</p>
    <dl className="pick-metrics">
      <div><dt>Bookmaker</dt><dd>{offer.bookmaker}</dd></div>
      <div><dt>Recorded price · not live</dt><dd>{offer.american_odds > 0 ? "+" : ""}{offer.american_odds}</dd></div>
      <div><dt>Modeled probability</dt><dd>{(offer.model_probability * 100).toFixed(1)}% <small>estimate</small></dd></div>
    </dl>
    <p className="pick-warning">{row.result === "SIMULATION_VOID" ? "Voided pick · excluded from Zeno’s tracked results; this does not confirm a sportsbook refund."
      : now >= Date.parse(offer.kickoff_utc) ? "Started fixture · publication is historical, not an upcoming choice."
      : now - Date.parse(offer.retrieved_at_utc) > 300_000 ? "Stale quote · price needs rechecking." : "Saved quote · recheck price and availability."}</p>
    <Information label="Pick information" context={title}>
      <p>Frozen publication price · Quote recorded <time dateTime={offer.retrieved_at_utc}>{offer.retrieved_at_utc}</time> · Published <time dateTime={row.published_at_utc}>{row.published_at_utc}</time></p>
      <p>Availability is not verified live. Last recorded check: {offer.availability_checked_at_utc}. Newer prices never replace this publication.</p>
      <p>Published {row.published_at_utc}; policy effective {row.policy_effective_at_utc}. Strategy version: {row.strategy_policy}.</p>
      <p>No-vig edge {(offer.no_vig_probability_edge * 100).toFixed(1)} pp · estimated EV {(offer.expected_profit * 100).toFixed(1)}%.</p>
      <p>{row.result === "SIMULATION_VOID" ? "Voided pick" : row.result} · simulated $2 risked · {row.profit_units === null ? "Pending" : `${row.profit_units.toFixed(2)} units / $${row.profit_dollars?.toFixed(2)}`}</p>
      <p>Simulated flat $2 risked per pick. No wagers are placed. Pushes return the stake; pending and voided picks are excluded from settled ROI.</p>
      {row.simulation_void && <p>Reviewed {row.simulation_void.recorded_at_utc}: {row.simulation_void.reason}. Simulated stake returned; review {row.simulation_void.record_hash}.</p>}
      <p>Publication: {row.publication_id} · Selection: {row.selection_id} · Provider: {offer.provider} · Fixture: {offer.provider_fixture_id}</p>
      {offer.market_type === "BTTS" ? <p>Forecast: {offer.forecast_id} · Comparison: {offer.comparison_id} · Observation: {offer.observation_id}</p>
        : <a href={`/opportunities/${offer.opportunity_id}`}>Recorded opportunity evidence</a>}
    </Information>
  </article>;
}
