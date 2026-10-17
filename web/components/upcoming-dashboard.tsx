"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";
import type { ProspectivePrediction, Recommendation } from "@/lib/api/types";
import type { BttsPerformance } from "@/lib/api/btts-performance";
import { ageLabel, fixtureKey, upcomingFixtures, loadUpcomingPicks, type MarketForecast, type UpcomingFixture } from "@/lib/upcoming";

import type { StrategyPick } from "@/lib/api/track-record";
import { PublishedPick } from "./strategy-picks";

type Snapshot = { predictions: ProspectivePrediction[]; btts: BttsPerformance["forecasts"];
  recommendations: Recommendation[]; picks: StrategyPick[]; errors: string[]; checked: number };
const kickoff = (value: string) => new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value));
function Market({ title, data, blocked, now }: { title: "Corners" | "BTTS"; data?: MarketForecast; blocked: boolean; now: number }) {
  return <section className="upcoming-market" aria-label={title}>
    <h3>{title}</h3>
    {!data ? <><strong>{blocked ? "Blocked · read unavailable" : "Waiting · no recorded forecast"}</strong><p>The acquisition reason is not available in the public feed.</p></> : <>
      <p className="ledger-meta">{data.research ? "Research forecast" : "Recorded forecast"} · Frozen {ageLabel(data.frozen, now)}</p>
      <dl className="ledger-values">{data.values.map((value, index) => <div key={index}>
        <dt>{title === "BTTS" ? ["Yes", "No"][index] : ["Home corners", "Away corners", "Total corners"][index]}</dt>
        <dd>{title === "BTTS" ? `${(value*100).toFixed(1)}%` : value.toFixed(2)}</dd>
      </div>)}</dl>
      {data.history && <p className="ledger-meta">History through {data.history}</p>}
      <details><summary>Frozen forecast reference</summary><p>{kickoff(data.frozen)} UTC · {data.version}</p><code>{data.id}</code></details>
    </>}
  </section>;
}

export function UpcomingDashboard() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([api.predictions(controller.signal), api.bttsPerformance(controller.signal), api.recommendations(controller.signal), loadUpcomingPicks(controller.signal)])
      .then(([predictions,btts,recommendations,picks]) => {
        if (controller.signal.aborted) return;
        const checked = Date.now(); setNow(checked);
        setData({ picks: picks.status === "fulfilled" ? picks.value : [], predictions: predictions.status === "fulfilled" ? predictions.value : [],
          btts: btts.status === "fulfilled" ? btts.value.forecasts : [],
          recommendations: recommendations.status === "fulfilled" ? recommendations.value : [],
          errors: [predictions.status === "rejected" ? "predictions" : "", btts.status === "rejected" ? "BTTS" : "",
            recommendations.status === "rejected" ? "legacy opportunities" : "", picks.status === "rejected" ? "published picks" : ""].filter(Boolean), checked });
        setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(timer); }, []);
  const refresh = () => { setLoading(true); setAttempt(v => v+1); };
  const forecastIncomplete = Boolean(data?.errors.some(source => ["predictions", "BTTS", "published picks"].includes(source)));
  const cards = new Map<string, UpcomingFixture>((data ? upcomingFixtures(data.predictions, data.btts, data.checked) : []).map(f => [f.key, f]));
  for (const row of data?.picks ?? []) {
    const key = fixtureKey(row.offer);
    if (!cards.has(key)) cards.set(key, { ...row.offer, key });
  }
  const fixtures = [...cards.values()].sort((a,b) => Date.parse(a.kickoff_utc)-Date.parse(b.kickoff_utc));
  return <div className="workspace ledger-workspace">
    <header className="page-heading"><div><p className="eyebrow">Recorded fixtures</p><h1>Next matches</h1><p>Published Model Picks and independent corner / BTTS forecasts, grouped by exact fixture.</p></div><button className="secondary-button" onClick={refresh} disabled={loading}>Refresh</button></header>
    <section className="panel ledger-panel upcoming-summary"><p>Forecast acquisition runs within 21–27 hours before kickoff, subject to valid data. A missing forecast does not mean a match is cancelled.</p>
      <p className="ledger-meta">This view covers recorded forecasts and published picks, not the full fixture calendar. Missing data and private acquisition block reasons are not available in the public feed. Research forecasts cover Championship (E1); other leagues are unsupported in that forecast view. Freeze age describes the record; it does not certify current data or prices.</p>
      <p><Link href="/predictions">All recorded predictions</Link> · <Link href="/performance">Corner results</Link> · <Link href="/research/btts/performance">BTTS results</Link></p>
    </section>
    {loading && <p role="status">Loading recorded upcoming fixtures…</p>}
    {data && <>
      {data.errors.length > 0 && <p className="error-banner" role="alert">Read unavailable: {data.errors.join(", ")}. {forecastIncomplete ? "Forecast coverage is incomplete." : "Legacy opportunity status is unavailable."} Use Refresh to retry.</p>}
      <p className="ledger-meta">{forecastIncomplete
        ? `${fixtures.length} fixtures visible from available records · total coverage unknown`
        : `${fixtures.length} unique fixtures with recorded forecasts or published picks`} · Checked {ageLabel(new Date(data.checked).toISOString(),now)}</p>
      {!fixtures.length && !loading && <section className="panel ledger-panel upcoming-summary"><h2>{forecastIncomplete ? "Upcoming coverage unavailable or incomplete" : "No upcoming forecasts or published picks recorded"}</h2><p>The public feed cannot establish the next fixture or its acquisition status. Historical results remain available above.</p></section>}
      <div className="ledger-list">{fixtures.map(f => <article className="panel ledger-card" key={f.key}>
        <div className="ledger-card-head"><div><small>{kickoff(f.kickoff_utc)} UTC</small><h2>{f.home_team} <span>vs</span> {f.away_team}</h2></div></div>
        {Date.parse(f.kickoff_utc) <= now && <p>Started fixture · retained from this read. Refresh for current fixtures.</p>}
        <section aria-label="Published Model Picks">
          <h3>Published Model Picks</h3>
          {data.errors.includes("published picks") ? <p>Published picks unavailable · no result inferred.</p> : data.picks.filter(row => fixtureKey(row.offer) === f.key).length > 0 ? data.picks.filter(row => fixtureKey(row.offer) === f.key).map(row => <div key={row.publication_id}>
            <strong>{row.strategy === "best_value" ? "Best Value" : "Higher Probability"}</strong>
            <PublishedPick row={row} now={now} />
          </div>) : <p>No qualifying published pick returned for this fixture in either strategy at this read. This does not describe legacy opportunities or research forecasts.</p>}
          <p><Link href="/recommendations">All published picks</Link> · <Link href="/recommendations?view=shortlist">Corner saved-price analysis · all fixtures</Link> · <Link href="/research/btts">BTTS research prices · all fixtures</Link></p>
          <p className="ledger-meta">These market-specific pages show saved observations with timestamps across all fixtures; they may not contain this fixture or a newer quote. Saved observations do not replace frozen publication odds or probabilities.</p>
        </section>
        {f.competition !== "E1" ? <p>Unsupported league for research forecasts · published picks above are independent.</p> : <div className="upcoming-markets"><Market title="Corners" data={f.corner} blocked={data.errors.includes("predictions")} now={now}/><Market title="BTTS" data={f.btts} blocked={data.errors.includes("BTTS") || data.errors.includes("predictions")} now={now}/></div>}
        <details><summary>Legacy opportunities</summary><p>{data.errors.includes("legacy opportunities") || now-data.checked >= 60000 ? "Legacy opportunity status unavailable · refresh to check." : data.recommendations.some(r => fixtureKey(r) === f.key)
          ? "A qualifying legacy corner opportunity was returned at this read. This is a separate feed from published Model Picks."
          : "No legacy corner opportunity returned at this read. Published Model Picks are independent."}</p></details>
      </article>)}</div>
      <p className="ledger-meta">Earliest frozen forecast per market and exact fixture is shown; timestamp ties use the immutable ID. Multiple cohorts and bookmakers do not increase fixture counts.</p>
    </>}
  </div>;
}
