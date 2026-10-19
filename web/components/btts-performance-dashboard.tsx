"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";
import type { BttsPerformance, BttsReportingScope } from "@/lib/api/btts-performance";

const percent = (v: number) => `${(v*100).toFixed(1)}%`;
export function BttsPerformanceDashboard() {
  const [data, setData] = useState<BttsPerformance | null>(null);
  const [competition, setCompetition] = useState<BttsReportingScope>("E1");
  const [asOf, setAsOf] = useState("");
  const [draftTime, setDraftTime] = useState("");
  const [timeError, setTimeError] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(false);
    api.bttsPerformance(controller.signal, { competition, asOf }).then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attempt, competition, asOf]);
  return <div className="workspace ledger-workspace btts-performance">
    <section className="page-heading"><div><p className="eyebrow">BTTS · Research only</p>
      <h1>BTTS outcomes &amp; performance</h1>
      <p>One result per exact fixture. The earliest frozen forecast is scored; other cohorts remain visible for audit.</p>
      <p>Bookmakers, Yes/No selections and repeated observations do not add independent matches. No sportsbook baseline is included.</p>
      <Link href="/performance">Corner performance</Link>{" · "}<Link href="/research/btts">BTTS research observations</Link>
    </div></section>
    <section className="panel ledger-panel" aria-label="Report filters">
      <label>League scope <select value={competition} onChange={event => setCompetition(event.target.value as BttsReportingScope)}>
        <option value="ALL">Overall (supported leagues)</option><option value="E1">Championship · E1</option><option value="E0">Premier League · E0</option>
      </select></label>
      <form onSubmit={event => {
        event.preventDefault();
        const value = draftTime.trim();
        if (value && (!/(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value)) || Date.parse(value) > Date.now())) {
          setTimeError(true); return;
        }
        setTimeError(false); setAsOf(value ? new Date(value).toISOString() : "");
      }}>
        <label>As of (ISO timestamp with timezone; blank for current) <input value={draftTime} onChange={event => setDraftTime(event.target.value)} placeholder="2026-10-19T12:00:00Z" /></label>
        <button className="secondary-button" type="submit">Apply history cutoff</button>
        {timeError ? <p role="alert">Enter a timestamp with timezone no later than now.</p> : null}
      </form>
      <p>Forecast quality only. Priced Model Picks, independent strategies and legacy quote simulations have separate return denominators.</p>
    </section>
    {error ? <section className="error-banner" role="alert">Could not load BTTS performance. <button className="secondary-button" onClick={() => setAttempt(v => v+1)}>Retry</button></section>
      : !data ? <p role="status">Loading BTTS outcomes…</p> : <>
        <section className="panel ledger-panel"><h2>Fixture sample</h2>
          <p>As of {data.as_of_utc}. Market: {data.market}.</p>
          {data.coverage === "UNSUPPORTED" ? <p>Premier League BTTS evidence is unavailable: production forecast collection and settlement support remain E1-only. This is not zero EPL performance.</p>
            : data.coverage === "PARTIAL" ? <p>Overall coverage is partial: Championship (E1) only. Premier League (E0) is unavailable and contributes no samples.</p> : null}
          <p>{data.unique_fixtures} unique fixtures · {data.forecast_cohorts} forecast cohorts · {data.settled_fixtures} settled · {data.unresolved_fixtures} unresolved · {data.upcoming_fixtures} upcoming</p>
          <p>Scored sample: n={data.metrics.sample_count}. Brier score: {data.metrics.brier_score?.toFixed(4) ?? "Not available"}. Log loss: {data.metrics.log_loss?.toFixed(4) ?? "Not available"}.</p>
          <p>Lower scores indicate more accurate probabilities. Log loss clips probabilities at 10⁻¹⁵. Missing results are excluded, never treated as No.</p>
        </section>
        <section className="panel ledger-panel"><h2>Descriptive calibration</h2><p>Each bin reports its own fixture count. Small samples are unstable and do not establish model quality.</p>
          {!data.metrics.calibration.length ? <p>No verified outcomes available for calibration.</p> : <div className="table-scroll"><table><thead><tr><th>Forecast Yes probability</th><th>Fixtures (n)</th><th>Mean probability</th><th>Observed Yes</th></tr></thead><tbody>
            {data.metrics.calibration.map(bin => <tr key={bin.lower}><td>{percent(bin.lower)}–{percent(bin.upper)}</td><td>{bin.sample_count}</td><td>{percent(bin.mean_probability)}</td><td>{percent(bin.observed_frequency)}</td></tr>)}
          </tbody></table></div>}
        </section>
        <section className="panel ledger-panel"><h2>Frozen forecasts &amp; actual results</h2>
          {!data.forecasts.length ? <p>No frozen {data.competition === "E0" ? "Premier League" : "Championship"} BTTS forecasts are available for this scope and cutoff.</p> : <div className="table-scroll"><table><thead><tr><th>Fixture / kickoff UTC</th><th>Original forecast</th><th>Sample role</th><th>Final score / BTTS</th><th>Evidence</th></tr></thead><tbody>
            {data.forecasts.map(row => <tr key={row.forecast_id}>
              <td>{row.home_team} – {row.away_team}<br />{new Date(row.kickoff_utc).toISOString()}<br /><small>{row.provider}: {row.provider_fixture_id}</small></td>
              <td>Yes {percent(row.yes_probability)} / No {percent(row.no_probability)}<br /><small>{row.model_version}<br />Frozen {new Date(row.frozen_at_utc).toISOString()}</small></td>
              <td>{row.selected_for_metrics ? "Primary fixture forecast" : "Additional cohort — excluded from metrics"}<br /><small>{row.origins.join(", ")}</small></td>
              <td>{row.status === "SETTLED" ? `${row.home_goals}–${row.away_goals} · ${row.actual_btts}` : row.status === "UPCOMING" ? "Upcoming — result missing" : "Unresolved — result missing"}</td>
              <td>{row.result_provider ?? "No verified final result"}{row.recorded_at_utc ? <><br />Recorded {new Date(row.recorded_at_utc).toISOString()}</> : null}
                <details><summary>Immutable references</summary><p>Forecast: <code>{row.forecast_id}</code></p>
                  {row.outcome_id ? <p>Outcome: <code>{row.outcome_id}</code><br />Result source SHA-256: <code>{row.result_source_sha256}</code></p> : null}
                  {row.corrections?.map(revision => <p key={revision.correction_id}>Reviewed correction {revision.recorded_at_utc}: {revision.reason}. Goals {revision.result.home_goals}–{revision.result.away_goals}. Supersedes {revision.supersedes_record_hash}; effective revision {revision.record_hash}. Original forecast and outcome retained.</p>)}
                  {row.source_data_hashes.map(s => <p key={s.filename}>{s.filename}: <code>{s.sha256}</code></p>)}
                </details></td>
            </tr>)}
          </tbody></table></div>}
        </section>
      </>}
  </div>;
}
