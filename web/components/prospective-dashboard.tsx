"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import type { ProspectiveOpportunity, ProspectivePerformance, ProspectivePrediction, ProspectiveStatus } from "@/lib/api/types";
import Link from "next/link";

const time = (value: string) => new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value)) + " UTC";
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const number = (value: number) => value.toFixed(2);
const price = (value: number) => `${value > 0 ? "+" : ""}${value}`;

function Status({ value }: { value: ProspectiveStatus }) {
  return <span className={`ledger-status ${value.toLowerCase()}`}>{value.replace("_", " ")}</span>;
}

function StateMessage({ loading, error, retry }: { loading: boolean; error: string | null; retry: () => void }) {
  if (loading) return <section className="panel ledger-message" role="status">Loading prospective evidence…</section>;
  if (error) return <section className="error-banner" role="alert"><strong>Could not load prospective evidence.</strong><span>{error}</span><button className="secondary-button" onClick={retry}>Retry</button></section>;
  return null;
}

export function PredictionsDashboard() {
  const [data, setData] = useState<{ predictions: ProspectivePrediction[]; opportunities: ProspectiveOpportunity[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    // Read opportunities first, then predictions: immutable opportunities cannot
    // outrun their parent prediction in the second read as collection appends.
    api.opportunities(controller.signal)
      .then(async (opportunities) => {
        const predictions = await api.predictions(controller.signal);
        const byId = new Map(predictions.map((item) => [item.prediction_id, item]));
        const counts = new Map<string, number>();
        if (opportunities.some((item) => {
          const parent = byId.get(item.prediction_id);
          counts.set(item.prediction_id, (counts.get(item.prediction_id) ?? 0) + 1);
          return !parent || parent.provider !== item.provider || parent.provider_fixture_id !== item.provider_fixture_id
            || parent.competition !== item.competition || parent.kickoff_utc !== item.kickoff_utc
            || parent.home_team !== item.home_team || parent.away_team !== item.away_team
            || parent.settlement_status !== item.settlement_status
            || (item.settlement_status === "SETTLED" && item.actual_team_corners !==
              (item.team_side === "HOME" ? parent.actual_home_corners : parent.actual_away_corners));
        }) || predictions.some((item) => (counts.get(item.prediction_id) ?? 0) !== item.opportunity_count)) {
          throw new ModelFCApiError("The API returned inconsistent predictions and opportunities. Retry to read a consistent snapshot.", "PROSPECTIVE_CONTRACT_MISMATCH", false);
        }
        if (!controller.signal.aborted) { setData({ predictions, opportunities }); setError(null); }
      })
      .catch((reason: unknown) => { if (!controller.signal.aborted) { setData(null); setError(reason instanceof Error ? reason.message : "Request failed."); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const retry = () => { setLoading(true); setError(null); setAttempt((value) => value + 1); };
  return <div className="workspace ledger-workspace">
    <header className="page-heading"><div><p className="eyebrow">Prospective evidence</p><h1>Predictions & opportunities</h1><p>Recorded before kickoff. Read only.</p></div></header>
    <StateMessage loading={loading} error={error} retry={retry} />
    {data && <>
      <section className="panel ledger-panel"><div className="section-title"><span>01</span><div><h2>Prediction runs</h2><p>{data.predictions.length} recorded</p></div></div>
        {!data.predictions.length ? <p className="ledger-message">No prospective predictions recorded yet.</p> : <div className="ledger-list">{data.predictions.map((item) => <article className="ledger-card" key={item.prediction_id}>
          <div className="ledger-card-head"><div><small>{item.competition} · {time(item.kickoff_utc)}</small><h3>{item.home_team} <span>vs</span> {item.away_team}</h3></div><Status value={item.settlement_status} /></div>
          <p className="ledger-meta">Created {time(item.created_at_utc)} · {item.model_name} / {item.model_version}</p>
          <dl className="ledger-values"><div><dt>Expected home</dt><dd>{number(item.expected_home_corners)}</dd></div><div><dt>Expected away</dt><dd>{number(item.expected_away_corners)}</dd></div><div><dt>Expected match</dt><dd>{number(item.expected_match_corners)}</dd></div><div><dt>Targets</dt><dd>{item.target_count}</dd></div><div><dt>Opportunities</dt><dd>{item.opportunity_count}</dd></div>{item.settlement_status === "SETTLED" && <div><dt>Actual corners</dt><dd>{item.actual_home_corners} · {item.actual_away_corners}</dd></div>}</dl>
        </article>)}</div>}
      </section>
      <section className="panel ledger-panel"><div className="section-title"><span>02</span><div><h2>Market opportunities</h2><p>{data.opportunities.length} recorded events · backend order</p></div></div>
        {!data.opportunities.length ? <p className="ledger-message">No qualifying opportunities recorded yet.</p> : <div className="ledger-list">{data.opportunities.map((item) => <article className="ledger-card" key={item.opportunity_id}>
          <div className="ledger-card-head"><div><small>{item.competition} · {time(item.kickoff_utc)}</small><h3>{item.home_team} <span>vs</span> {item.away_team}</h3></div><Status value={item.settlement_status} /></div>
          <p className="ledger-offer">{item.team} {item.direction} {item.line} <strong>{price(item.american_odds)}</strong> <span>· {item.bookmaker}</span></p>
          <dl className="ledger-values"><div><dt>Model decisive</dt><dd>{percent(item.model_decisive_probability)}</dd></div><div><dt>No-vig market</dt><dd>{percent(item.no_vig_market_probability)}</dd></div><div><dt>No-vig edge</dt><dd className={item.no_vig_probability_edge >= 0 ? "positive" : "negative"}>{price(Number((item.no_vig_probability_edge * 100).toFixed(1)))} pp</dd></div>{item.result && <><div><dt>Result</dt><dd>{item.result}</dd></div><div><dt>Actual team corners</dt><dd>{item.actual_team_corners}</dd></div><div><dt>Profit units</dt><dd>{item.realized_profit_units?.toFixed(2)}</dd></div></>}</dl>
          <p className="ledger-meta">Qualified {time(item.qualified_at_utc)}</p>
          <Link className="text-button" href={`/opportunities/${item.opportunity_id}`}>View opportunity evidence</Link>
        </article>)}</div>}
      </section>
    </>}
  </div>;
}

const metric = (value: number | null) => value === null ? "—" : number(value);
const rate = (value: number | null) => value === null ? "—" : percent(value);

export function PerformanceDashboard() {
  const [data, setData] = useState<ProspectivePerformance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.performance(controller.signal).then((result) => { if (!controller.signal.aborted) { setData(result); setError(null); } })
      .catch((reason: unknown) => { if (!controller.signal.aborted) { setData(null); setError(reason instanceof Error ? reason.message : "Request failed."); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  return <div className="workspace ledger-workspace">
    <header className="page-heading"><div><p className="eyebrow">Prospective evidence</p><h1>Performance</h1><p>Frozen pre-kickoff forecasts, validated results, and market outcomes. Backend calculated.</p></div></header>
    <StateMessage loading={loading} error={error} retry={() => { setLoading(true); setError(null); setAttempt((value) => value + 1); }} />
    {data && <>{data.model_performance.settled_prediction_runs === 0 && <p className="panel ledger-message">Not enough settled evidence yet. Counts are shown; error and probability metrics will appear after results are validated.</p>}
      <section className="panel ledger-panel"><div className="section-title"><span>01</span><div><h2>Model quality</h2><p>Count error: actual − predicted · positive bias means underprediction</p></div></div>
        <dl className="performance-grid"><div><dt>Settled prediction runs</dt><dd>{data.model_performance.settled_prediction_runs}</dd></div><div><dt>Team forecasts</dt><dd>{data.model_performance.settled_team_forecasts}</dd></div><div><dt>Team-corner MAE</dt><dd>{metric(data.model_performance.team_corner_mae)}</dd></div><div><dt>Team-corner RMSE</dt><dd>{metric(data.model_performance.team_corner_rmse)}</dd></div><div><dt>Team-corner bias</dt><dd>{metric(data.model_performance.team_corner_mean_error)}</dd></div><div><dt>Match-total MAE</dt><dd>{metric(data.model_performance.match_total_mae)}</dd></div><div><dt>Match-total RMSE</dt><dd>{metric(data.model_performance.match_total_rmse)}</dd></div><div><dt>Match-total bias</dt><dd>{metric(data.model_performance.match_total_mean_error)}</dd></div></dl>
        <p className="ledger-meta">{data.model_performance.total_prediction_runs} prediction runs · {data.model_performance.total_unique_prediction_targets} unique targets · {data.model_performance.supported_prediction_targets} supported · {data.model_performance.unsettled_supported_prediction_targets} unsettled</p>
        <p className="ledger-meta">Model versions: {data.model_performance.model_versions.length ? data.model_performance.model_versions.map((item) => `${item.model_name} / ${item.model_version} (${item.settled_prediction_runs} settled of ${item.total_prediction_runs})`).join(" · ") : "None yet"}</p>
      </section>
      <section className="panel ledger-panel"><div className="section-title"><span>02</span><div><h2>Probability quality</h2><p>Settled decisive team-total targets · pushes excluded from binary scores</p></div></div>
        <dl className="performance-grid"><div><dt>Decisive targets scored</dt><dd>{data.model_performance.decisive_probability_targets_scored}</dd></div><div><dt>Brier score</dt><dd>{metric(data.model_performance.brier_score)}</dd></div><div><dt>Log loss</dt><dd>{metric(data.model_performance.log_loss)}</dd></div><div><dt>Pushes excluded</dt><dd>{data.model_performance.pushes_excluded_from_decisive_scoring}</dd></div></dl>
        {data.model_performance.decisive_probability_targets_scored < 30 && <p className="ledger-meta">Small sample: not enough settled decisive targets to interpret calibration reliably.</p>}
        <div className="performance-calibration"><h3>Calibration · {data.model_performance.decisive_probability_targets_scored} decisive targets</h3><table><thead><tr><th>Probability range</th><th>Mean predicted</th><th>Observed win rate</th><th>N</th></tr></thead><tbody>{data.model_performance.calibration.map((bucket) => <tr key={bucket.lower_bound}><td>{Math.round(bucket.lower_bound * 100)}–{Math.round(bucket.upper_bound * 100)}%</td><td>{rate(bucket.mean_predicted_probability)}</td><td>{rate(bucket.observed_win_rate)}</td><td>{bucket.sample_count}</td></tr>)}</tbody></table><p className="ledger-meta">Lower bounds included; upper bounds excluded except 100%, which is included.</p></div>
      </section>
      <section className="panel ledger-panel"><div className="section-title"><span>03</span><div><h2>Opportunity performance</h2><p>One unit risked per settled market event</p></div></div><dl className="performance-grid"><div><dt>Settled opportunities</dt><dd>{data.opportunity_performance.settled_opportunities}</dd></div><div><dt>W-L-P</dt><dd>{data.opportunity_performance.wins}-{data.opportunity_performance.losses}-{data.opportunity_performance.pushes}</dd></div><div><dt>Decisive win rate</dt><dd>{rate(data.opportunity_performance.win_rate_excluding_pushes)}</dd></div><div><dt>Realized profit, units</dt><dd>{number(data.opportunity_performance.realized_profit_units)}</dd></div><div><dt>ROI on settled opportunities</dt><dd>{rate(data.opportunity_performance.roi_on_settled_opportunities)}</dd></div></dl><p className="ledger-meta">{data.opportunity_performance.total_opportunity_events} events · {data.opportunity_performance.unresolved_open_opportunities} unresolved</p></section>
    </>}
  </div>;
}
