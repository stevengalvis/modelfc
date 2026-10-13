"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { RecordedFixture, RecordedOffer, RecordedOffers, RecordedOfferStatus } from "@/lib/api/types";

const percent = (value: number | null) => value === null ? "Not available" : `${(value * 100).toFixed(1)}%`;
const signed = (value: number, digits = 0) => `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
const utc = (value: string) => new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "medium", timeZone: "UTC",
}).format(new Date(value)) + " UTC";
const age = (seconds: number) => seconds < 60 ? `${Math.floor(seconds)}s` : seconds < 3600
  ? `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s` : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
const labels: Record<RecordedOfferStatus, string> = {
  RECENT_QUALIFYING: "Recently checked · qualifying", PRICE_NEEDS_RECHECKING: "Price needs rechecking",
  QUOTE_UNAVAILABLE: "Quote unavailable at last check", NO_QUALIFYING_EDGE: "No qualifying edge",
  VALUE_UNVERIFIED: "Value comparison incomplete", REVIEW_REQUIRED: "Price needs review", HISTORICAL: "Historical quote · kickoff passed",
};

function displayStatus(item: RecordedOffer, asOf: number, elapsed: number, failed: boolean): RecordedOfferStatus {
  if (item.status === "HISTORICAL" || asOf + elapsed * 1000 >= Date.parse(item.kickoff_utc)) return "HISTORICAL";
  if (["QUOTE_UNAVAILABLE", "REVIEW_REQUIRED"].includes(item.status)) return item.status;
  if (failed || item.observation_age_seconds + elapsed > 300) return "PRICE_NEEDS_RECHECKING";
  return item.status;
}

function Offer({ item, asOf, elapsed, failed }: { item: RecordedOffer; asOf: number; elapsed: number; failed: boolean }) {
  const status = displayStatus(item, asOf, elapsed, failed);
  return <div className="recorded-offer">
    <div className="recorded-offer-heading"><h3>{item.team} {item.direction.toLowerCase()} {item.line} corners</h3>
      <span className={`recorded-status ${status === "RECENT_QUALIFYING" ? "recent" : ""}`}>{labels[status]}</span></div>
    <div className="recorded-price"><strong>{item.bookmaker} · {signed(item.american_odds)}</strong>
      <span>Recorded price · {item.decimal_odds.toFixed(2)} decimal</span></div>
    <dl className="recorded-metrics">
      <div><dt>Model probability</dt><dd>{percent(item.model_probability)}</dd></div>
      <div><dt>No-vig edge at quote</dt><dd>{item.no_vig_probability_edge === null ? "Not available" : `${signed(item.no_vig_probability_edge * 100, 1)} pp`}</dd></div>
      <div><dt>Expected value at quote</dt><dd>{item.expected_profit === null ? "Not available" : `${signed(item.expected_profit * 100, 1)}% per unit risked`}</dd></div>
    </dl>
    <p className="ledger-meta">Observed <time dateTime={item.retrieved_at_utc}>{utc(item.retrieved_at_utc)}</time> · Age {age(item.observation_age_seconds + elapsed)}</p>
    {item.availability_checked_at_utc && <p className="ledger-meta">Last availability check <time dateTime={item.availability_checked_at_utc}>{utc(item.availability_checked_at_utc)}</time></p>}
    {item.opportunity_id ? <Link href={`/opportunities/${item.opportunity_id}`}>Recorded opportunity evidence</Link>
      : <Link href={`/predictions#prediction-${item.prediction_id}`}>Recorded forecast evidence</Link>}
  </div>;
}

function Fixture({ item, asOf, elapsed, failed }: { item: RecordedFixture; asOf: number; elapsed: number; failed: boolean }) {
  // Backend order is authoritative. Show one book per target in the summary;
  // every returned book/line remains accessible in the expanded recorded detail.
  const seen = new Set<string>();
  const summary = item.offers.filter(o => { if (seen.has(o.target_id)) return false; seen.add(o.target_id); return true; }).slice(0, 3);
  const historical = item.status === "HISTORICAL" || asOf + elapsed * 1000 >= Date.parse(item.kickoff_utc);
  const primary = item.forecasts.find(p => p.prediction_id === item.selected_prediction_id)
    ?? item.forecasts.find(p => p.prediction_type === "CORNER");
  return <article className="panel recorded-fixture">
    <header><p className="ledger-meta">{item.competition} · {historical ? "Kicked off / historical" : "Upcoming"}</p>
      <h2>{item.home_team} vs {item.away_team}</h2>
      <p className="ledger-meta">Kickoff <time dateTime={item.kickoff_utc}>{utc(item.kickoff_utc)}</time></p>
      {primary && <p>Recorded expected corners: {item.home_team} <strong>{primary.expected_home_corners?.toFixed(1)}</strong> · {item.away_team} <strong>{primary.expected_away_corners?.toFixed(1)}</strong></p>}
    </header>
    {summary.length ? <><p className="recorded-note">Ranked corner value at recorded prices. Older quotes are not current recommendations.</p>
      {summary.map(o => <Offer key={`${o.target_id}:${o.bookmaker}`} item={o} asOf={asOf} elapsed={elapsed} failed={failed} />)}
      {item.offers.length > summary.length && <details className="recorded-details"><summary>All {item.offers.length} recorded offers · bookmakers and lines</summary>
        {item.offers.map(o => <Offer key={`${o.target_id}:${o.bookmaker}`} item={o} asOf={asOf} elapsed={elapsed} failed={failed} />)}
      </details>}</> : <p className="recorded-note">No recorded corner odds. Forecasts remain visible; price and value are unavailable.</p>}
    <details className="recorded-details"><summary>{item.forecasts.length} recorded {item.forecasts.length === 1 ? "forecast" : "forecasts"} · model and evidence</summary>
      <p>Offers use the latest frozen capture forecast, not whichever model gives the highest value.</p>
      {item.forecasts.map(p => <div key={p.prediction_id} className="recorded-forecast">
        <strong>{p.prediction_type === "BTTS" ? "BTTS research · not a recommendation" : `Corner forecast${p.research_only ? " · research only" : ""}`}</strong>
        {p.prediction_type === "BTTS" && <p>BTTS Yes probability: {percent(p.btts_yes_probability)}</p>}
        <p className="ledger-meta">Frozen {utc(p.created_at_utc)} · {p.model_name} · {p.model_version}</p>
        <p className="ledger-meta">Forecast {p.prediction_id}</p>
      </div>)}
      <Link href="/predictions">Forecast ledger</Link> · <Link href="/research/btts">BTTS probabilities and recorded odds · research only</Link>
    </details>
  </article>;
}

export function RecommendationsDashboard() {
  const [data, setData] = useState<RecordedOffers | null>(null);
  const [received, setReceived] = useState(0);
  const [tick, setTick] = useState(0);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const started = performance.now();
    let timer: number | undefined;
    const refresh = () => setAttempt(value => value + 1);
    window.addEventListener("focus", refresh);
    api.recordedOffers(controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value); setReceived(started); setTick(performance.now()); setFailed(false); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); }).finally(() => {
      if (!controller.signal.aborted) timer = window.setTimeout(refresh, 60_000);
    });
    return () => { controller.abort(); window.clearTimeout(timer); window.removeEventListener("focus", refresh); };
  }, [attempt]);
  const elapsed = Math.max(0, tick - received) / 1000;
  const asOf = data ? Date.parse(data.as_of_utc) : 0;
  const upcoming = data?.fixtures.filter(f => f.status === "UPCOMING" && Date.parse(f.kickoff_utc) > asOf + elapsed * 1000) ?? [];
  const historical = data?.fixtures.filter(f => !upcoming.includes(f)) ?? [];
  return <div className="workspace recommendations-workspace">
    <header className="page-heading"><div><span className="eyebrow">Recorded market</span><h1>Recommendations</h1>
      <p>Corner forecasts and last-known prices. Recheck older prices before relying on their recorded value.</p></div></header>
    <p className="recorded-note">This page checks saved data every minute; it does not fetch sportsbook odds. A scheduler check does not guarantee new prices. Next eligible odds refresh is not available here.</p>
    {failed && <section className="panel ledger-message" role="alert"><h2>Saved offers could not be refreshed</h2>
      <p>{data ? "Showing the previous read. Prices need rechecking." : "Recorded offers are unavailable. Please try again."}</p>
      <button className="secondary-button" onClick={() => setAttempt(v => v + 1)}>Retry</button></section>}
    {!data && !failed && <section className="panel ledger-message" role="status">Loading recorded offers…</section>}
    {data && <><p className="ledger-meta">Saved data read <time dateTime={data.as_of_utc}>{utc(data.as_of_utc)}</time></p>
      {!upcoming.length && <section className="panel ledger-message"><h2>No upcoming recorded fixtures</h2><p>Fixtures appear when a forecast has been recorded.</p></section>}
      <div className="recorded-fixtures">{upcoming.map(item => <Fixture key={item.fixture_id} item={item} asOf={asOf} elapsed={elapsed} failed={failed} />)}</div>
      {historical.length > 0 && <details className="recorded-details"><summary>Kicked-off / historical fixtures ({historical.length})</summary>
        <div className="recorded-fixtures">{historical.map(item => <Fixture key={item.fixture_id} item={item} asOf={asOf} elapsed={elapsed} failed={failed} />)}</div>
      </details>}</>}
  </div>;
}
