"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import type { ProspectiveOpportunity, ProspectivePerformance, ProspectivePrediction, ProspectiveStatus } from "@/lib/api/types";

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
          return !parent || parent.provider_fixture_id !== item.provider_fixture_id
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
        </article>)}</div>}
      </section>
    </>}
  </div>;
}

const modelLabels = {
  total_prediction_runs: "Prediction runs", total_unique_prediction_targets: "Unique targets",
  supported_prediction_targets: "Supported targets", settled_prediction_targets: "Settled targets",
  unsettled_supported_prediction_targets: "Unsettled supported targets",
} as const;
const opportunityLabels = {
  total_opportunity_events: "Opportunity events", settled_opportunities: "Settled opportunities",
  wins: "Wins", losses: "Losses", pushes: "Pushes", win_rate_excluding_pushes: "Win rate, excluding pushes",
  realized_profit_units: "Realized profit, units", unresolved_open_opportunities: "Unresolved open opportunities",
} as const;

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
    <header className="page-heading"><div><p className="eyebrow">Prospective evidence</p><h1>Performance</h1><p>Backend calculated totals from recorded predictions and settled opportunities.</p></div></header>
    <StateMessage loading={loading} error={error} retry={() => { setLoading(true); setError(null); setAttempt((value) => value + 1); }} />
    {data && <>{data.model_performance.total_prediction_runs === 0 && <p className="panel ledger-message">No prospective predictions recorded yet. Performance will appear as evidence accumulates.</p>}
      <section className="panel ledger-panel"><div className="section-title"><span>01</span><div><h2>Model performance</h2><p>Prediction and target coverage</p></div></div><dl className="performance-grid">{(Object.keys(modelLabels) as Array<keyof typeof modelLabels>).map((key) => <div key={key}><dt>{modelLabels[key]}</dt><dd>{data.model_performance[key]}</dd></div>)}</dl></section>
      <section className="panel ledger-panel"><div className="section-title"><span>02</span><div><h2>Opportunity performance</h2><p>Settled market events</p></div></div><dl className="performance-grid">{(Object.keys(opportunityLabels) as Array<keyof typeof opportunityLabels>).map((key) => <div key={key}><dt>{opportunityLabels[key]}</dt><dd>{key === "win_rate_excluding_pushes" ? data.opportunity_performance[key] === null ? "—" : percent(data.opportunity_performance[key]) : key === "realized_profit_units" ? number(data.opportunity_performance[key]) : data.opportunity_performance[key]}</dd></div>)}</dl></section>
    </>}
  </div>;
}
