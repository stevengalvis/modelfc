"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";
import type { ProspectivePrediction, Recommendation } from "@/lib/api/types";
import type { BttsPerformance } from "@/lib/api/btts-performance";
import { ageLabel, fixtureKey, upcomingFixtures, type MarketForecast } from "@/lib/upcoming";

type Snapshot = { predictions: ProspectivePrediction[]; btts: BttsPerformance["forecasts"];
  recommendations: Recommendation[]; errors: string[]; checked: number };
const kickoff = (value: string) => new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value));
function Market({ title, data, blocked, now }: { title: "Corners" | "BTTS"; data?: MarketForecast; blocked: boolean; now: number }) {
  return <section className="upcoming-market" aria-label={title}>
    <h3>{title}</h3>
    {!data ? <><strong>{blocked ? "Blocked · read unavailable" : "Waiting · no recorded forecast"}</strong><p>The acquisition reason is not available in the public feed.</p></> : <>
      <p className="ledger-meta">{data.research ? "Research only" : "Recorded forecast"} · Frozen {ageLabel(data.frozen, now)}</p>
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
    Promise.allSettled([api.predictions(controller.signal), api.bttsPerformance(controller.signal), api.recommendations(controller.signal)])
      .then(([predictions,btts,recommendations]) => {
        if (controller.signal.aborted) return;
        const checked = Date.now(); setNow(checked);
        setData({ predictions: predictions.status === "fulfilled" ? predictions.value : [],
          btts: btts.status === "fulfilled" ? btts.value.forecasts : [],
          recommendations: recommendations.status === "fulfilled" ? recommendations.value : [],
          errors: [predictions.status === "rejected" ? "predictions" : "", btts.status === "rejected" ? "BTTS" : "",
            recommendations.status === "rejected" ? "recommendations" : ""].filter(Boolean), checked });
        setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(timer); }, []);
  const refresh = () => { setLoading(true); setAttempt(v => v+1); };
  const forecastIncomplete = Boolean(data?.errors.some(source => source !== "recommendations"));
  const fixtures = data ? upcomingFixtures(data.predictions, data.btts, now) : [];
  return <div className="workspace ledger-workspace">
    <header className="page-heading"><div><p className="eyebrow">Championship · E1</p><h1>Next matches</h1><p>One card per exact fixture, with independently recorded corner and BTTS forecasts.</p></div><button className="secondary-button" onClick={refresh} disabled={loading}>Refresh</button></header>
    <section className="panel ledger-panel upcoming-summary"><p>Forecast acquisition runs within 21–27 hours before kickoff, subject to valid data. A missing forecast does not mean a match is cancelled.</p>
      <p className="ledger-meta">This view covers recorded forecasts, not the full fixture calendar. Fixtures with neither forecast and private acquisition block reasons are not available in the public feed. Freeze age describes the record; it does not certify current data or prices.</p>
      <p><Link href="/predictions">All recorded predictions</Link> · <Link href="/performance">Corner results</Link> · <Link href="/research/btts/performance">BTTS results</Link></p>
    </section>
    {loading && <p role="status">Loading recorded upcoming fixtures…</p>}
    {data && <>
      {data.errors.length > 0 && <p className="error-banner" role="alert">Read unavailable: {data.errors.join(", ")}. {forecastIncomplete ? "Forecast coverage is incomplete." : "Recommendation status is unavailable."} Use Refresh to retry.</p>}
      <p className="ledger-meta">{forecastIncomplete
        ? `${fixtures.length} fixtures visible from available records · total coverage unknown`
        : `${fixtures.length} unique upcoming fixtures with recorded forecasts`} · Checked {ageLabel(new Date(data.checked).toISOString(),now)}</p>
      {!fixtures.length && !loading && <section className="panel ledger-panel upcoming-summary"><h2>{forecastIncomplete ? "Upcoming coverage unavailable or incomplete" : "No upcoming forecasts recorded"}</h2><p>The public feed cannot establish the next fixture or its acquisition status. Historical results remain available above.</p></section>}
      <div className="ledger-list">{fixtures.map(f => <article className="panel ledger-card" key={f.key}>
        <div className="ledger-card-head"><div><small>{kickoff(f.kickoff_utc)} UTC</small><h2>{f.home_team} <span>vs</span> {f.away_team}</h2></div></div>
        <div className="upcoming-markets"><Market title="Corners" data={f.corner} blocked={data.errors.includes("predictions")} now={now}/><Market title="BTTS" data={f.btts} blocked={data.errors.includes("BTTS") || data.errors.includes("predictions")} now={now}/></div>
        <p className="ledger-meta">{data.errors.includes("recommendations") || now-data.checked >= 60000 ? "Recommendation status unavailable · refresh to check existing gates." : data.recommendations.some(r => fixtureKey(r) === f.key)
          ? <Link href="/recommendations">Qualified corner recommendation in the existing feed · view current gates</Link>
          : "No qualified recommendation returned by the existing feed."} BTTS is research only.</p>
      </article>)}</div>
      <p className="ledger-meta">Earliest frozen forecast per market and exact fixture is shown; timestamp ties use the immutable ID. Multiple cohorts and bookmakers do not increase fixture counts.</p>
    </>}
  </div>;
}
