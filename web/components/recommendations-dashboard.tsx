"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { Recommendation } from "@/lib/api/types";

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const signed = (value: number, digits: number) => `${value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(digits)}`;
const utc = (value: string) => new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
}).format(new Date(value)) + " UTC";

function RecommendationCard({ item }: { item: Recommendation }) {
  return <article className="panel recommendation-card">
    <p className="ledger-meta">{item.home_team} vs {item.away_team} · {item.competition}</p>
    <p className="ledger-meta">Kickoff <time dateTime={item.kickoff_utc}>{utc(item.kickoff_utc)}</time></p>
    <h2>{item.team} {item.direction} {item.line} CORNERS</h2>
    <div className="recommendation-offer">
      <p>{item.bookmaker} <strong>{signed(item.american_odds, 0)}</strong></p>
      <span>Best eligible price across supported books</span>
    </div>
    <dl className="ledger-values recommendation-metrics">
      <div><dt>Model probability</dt><dd>{percent(item.model_probability)}</dd></div>
      <div><dt>No-vig market probability</dt><dd>{percent(item.no_vig_market_probability)}</dd></div>
      <div><dt>No-vig edge</dt><dd>{signed(item.no_vig_probability_edge * 100, 1)} pp</dd></div>
      <div><dt>EV · expected profit per $1 risked</dt><dd>{item.expected_profit >= 0 ? "+" : "-"}${Math.abs(item.expected_profit).toFixed(2)} / $1</dd></div>
    </dl>
    <div className="recommendation-freshness ledger-meta">
      <p>Retrieved <time dateTime={item.retrieved_at_utc}>{utc(item.retrieved_at_utc)}</time> · Quote age at read: {Math.round(item.observation_age_seconds)}s</p>
      <p>Availability checked <time dateTime={item.availability_checked_at_utc}>{utc(item.availability_checked_at_utc)}</time></p>
    </div>
  </article>;
}

export function RecommendationsDashboard() {
  const [items, setItems] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.recommendations(controller.signal).then((value) => {
      if (!controller.signal.aborted) setItems(value);
    }).catch(() => {
      // Never display API messages, exception text or malformed response contents.
      if (!controller.signal.aborted) setFailed(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt]);

  return <div className="workspace recommendations-workspace">
    <header className="page-heading"><div><span className="eyebrow">Current market</span>
      <h1>Recommendations</h1>
      <p>Current qualified team-corner opportunities using fresh sportsbook prices.</p></div>
    </header>
    {loading ? <section className="panel ledger-message" role="status">Loading recommendations…</section>
      : failed ? <section className="panel ledger-message" role="alert">
        <h2>Recommendations unavailable</h2>
        <p>Could not load current recommendations. Please try again.</p>
        <button className="secondary-button" onClick={() => { setLoading(true); setFailed(false); setAttempt((value) => value + 1); }}>Retry</button>
      </section>
      : items.length === 0 ? <section className="panel ledger-message">
        <h2>No current recommendations</h2>
        <p>Zeno will surface qualified team-corner opportunities here when fresh sportsbook prices are available.</p>
      </section>
      : <ol className="recommendation-list" aria-label="Recommendations in backend value order">
        {items.map((item) => <li key={item.target_id}><RecommendationCard item={item} /></li>)}
      </ol>}
  </div>;
}
