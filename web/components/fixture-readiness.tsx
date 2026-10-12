"use client";
import { useEffect, useState } from "react";
import { getFixtureReadiness, type FixtureReadiness } from "@/lib/api/fixture-readiness";

export function FixtureReadinessDashboard() {
  const [data, setData] = useState<FixtureReadiness | null>(null);
  const [error, setError] = useState(false);
  const [all, setAll] = useState(false);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    let active = true;
    getFixtureReadiness().then(v => { if (active) setData(v); }).catch(() => { if (active) setError(true); });
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const stale = data?.calendar_retrieved_at_utc && now - Date.parse(data.calendar_retrieved_at_utc) > 6 * 3600_000;
  const fixtures = data?.fixtures.filter(f => all || f.status !== "FINISHED") ?? [];
  return <section className="fixture-readiness">
    <h1>Saved fixture status</h1>
    <p>Championship and Premier League data readiness. Saved source evidence, not live scores or betting recommendations.</p>
    {error ? <p role="alert">Saved fixture status is unavailable. No calendar completeness or forecast claim can be made.</p> : !data ? <p role="status">Loading saved fixture status…</p> : <>
      <h2>Championship · E1</h2>
      <p>Calendar: <strong>{stale ? "STALE" : data.calendar_state}</strong>. Fresh means retrieved within six hours; it does not establish history or price readiness.</p>
      {data.calendar_retrieved_at_utc && <p>Source retrieved: <time>{data.calendar_retrieved_at_utc}</time><br />Projection generated: <time>{data.generated_at_utc}</time></p>}
      {data.calendar_state === "MISSING" || data.calendar_state === "UNAVAILABLE" ? <p>A reviewed saved calendar projection must be published before fixtures can appear.</p> : <>
        <p>Showing {fixtures.length} of {data.fixtures.length} saved season fixtures. Statuses are as of source retrieval. Past kickoffs without a final status remain visible.</p>
        <label><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} /> Include finished fixtures</label>
    </>}
      <p>A missing recorded forecast has no verified per-fixture acquisition reason. The automatic E1 forecast window is 21–27 hours before kickoff; this view cannot promise a forecast. Recorded means a saved research forecast exists, not that a price or recommendation is available.</p>
      <h2>Premier League · E0</h2>
      <p>Calendar missing: a reviewed E0 saved-calendar adapter is required. Team-total corners are supported locally; E0 BTTS is unsupported.</p>
      <h2>History and prices</h2>
      <p>Goals, corners and shots coverage has not been assessed by this projection. Public history authorization and freshness must be verified separately. No bookmaker prices are included.</p>
      <div className="fixture-status-grid">{fixtures.map(f => <article key={f.fixture_id}>
        <h3>{f.home_team} v {f.away_team}</h3>
        <p><time>{f.kickoff_utc}</time> · {f.status}</p>
        {f.markets.filter(m => m.state === "RECORDED").length === 1 && <p>Partial forecast coverage</p>}
        {f.markets.map(m => <p key={m.market}><strong>{m.market === "CORNER" ? "Team corners" : "BTTS research"}:</strong> {m.state === "RECORDED" ? `Recorded at ${m.frozen_at_utc}` : "No recorded forecast in this export; reason unknown"}</p>)}
      </article>)}</div>
    </>}
  </section>;
}
