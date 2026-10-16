"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { TrackRecord } from "@/lib/api/track-record";
import type { ShortlistRanking } from "@/lib/api/types";
import { MODEL_PICKS_LABEL, TRACK_RECORD_LABEL, TRACK_RECORD_SUBTITLE } from "@/lib/track-record-labels";

const rate = (value: number | null) => value === null ? "—" : `${(value*100).toFixed(1)}%`;
export function PublishedTrackRecord() {
  const [data, setData] = useState<TrackRecord | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState<ShortlistRanking>("best_value");
  useEffect(() => {
    const controller = new AbortController();
    api.trackRecord(controller.signal).then(value => { if (!controller.signal.aborted) { setData(value); setError(false); } })
      .catch(() => { if (!controller.signal.aborted) { setData(null); setError(true); } });
    return () => controller.abort();
  }, [attempt]);
  const summary = data?.by_view[view];
  const rows = data?.publications.filter(r => r.ranking_views.includes(view)) ?? [];
  return <section className="panel ledger-panel" aria-label={TRACK_RECORD_LABEL}>
    <h2>{TRACK_RECORD_LABEL}</h2><p>{TRACK_RECORD_SUBTITLE}</p>
    <p>One frozen selection per match and market: each team’s corner total, or Championship BTTS Yes/No. Best Value selects the outcome; the best eligible bookmaker supplies its saved price. Higher Probability is the subset with at least 65% modeled win probability. Overlapping views count once.</p>
    {error ? <div role="alert"><p>Published recommendations could not be loaded. No result is inferred.</p><button onClick={() => { setError(false); setAttempt(n => n+1); }}>Retry published record</button></div>
      : !data ? <p role="status">Loading published recommendations…</p> : null}
    {data && summary && <>
      <label>Published view <select value={view} onChange={e => setView(e.target.value as ShortlistRanking)}>
        <option value="best_value">Best Value · all published picks</option><option value="higher_probability">Higher Probability · 65% or more</option>
      </select></label>
      {!data.summary.picks && <p>No published recommendations yet. Tracking starts with the first successful publication after activation; historical quotes are not backfilled.</p>}
      <dl className="performance-grid"><div><dt>Published picks</dt><dd>{summary.picks}</dd></div>
        <div><dt>Unique fixtures</dt><dd>{summary.unique_fixtures}</dd></div>
        <div><dt>W–L–Push</dt><dd>{summary.wins}–{summary.losses}–{summary.pushes}</dd></div>
        <div><dt>Pending</dt><dd>{summary.pending}</dd></div><div><dt>Win rate, excluding pushes</dt><dd>{rate(summary.win_rate)}</dd></div>
        <div><dt>Profit, units</dt><dd>{summary.profit_units.toFixed(2)}</dd></div><div><dt>ROI on settled picks</dt><dd>{rate(summary.roi)}</dd></div></dl>
      <p>One hypothetical unit risked per published pick, at its original price. Pushes return the stake and remain in the ROI denominator. Missing results remain pending. Sportsbook void settlement is not available yet; these are not actual wager returns.</p>
      {data.by_league_market.map(group => <p key={`${group.competition}:${group.market_type}`}>{group.competition} · {group.market_type === "BTTS" ? "Both teams to score" : "Team corners"} · {group.summary.picks} published picks across both views, counted once</p>)}
      {rows.length > 0 && <div className="performance-calibration"><table><caption>{MODEL_PICKS_LABEL} · published selections and original prices</caption>
        <thead><tr><th>Match / league</th><th>Selection</th><th>Book / odds</th><th>Publication / quote time (UTC)</th><th>Result</th><th>Profit (units)</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.publication_id}><td>{row.offer.home_team} vs {row.offer.away_team} · {row.offer.competition}</td>
          <td>{row.offer.market_type === "BTTS" ? `Both teams to score: ${row.offer.side === "YES" ? "Yes" : "No"}` : `${row.offer.team} corners ${row.offer.direction.toLowerCase()} ${row.offer.line}`}</td>
          <td>{row.offer.bookmaker} {row.offer.american_odds > 0 ? "+" : ""}{row.offer.american_odds}</td>
          <td><time dateTime={row.published_at_utc}>{new Date(row.published_at_utc).toISOString()}</time><br /><time dateTime={row.offer.retrieved_at_utc}>{new Date(row.offer.retrieved_at_utc).toISOString()}</time></td>
          <td>{row.result}</td><td>{row.profit_units === null ? "—" : row.profit_units.toFixed(2)}</td></tr>)}</tbody>
      </table></div>}
    </>}
  </section>;
}
