"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import { describeApiError, ModelFCApiError } from "@/lib/api/errors";
import type { OpportunityDetail } from "@/lib/api/types";

const amount = (value: number) => value.toFixed(2);
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const odds = (value: number) => value > 0 ? `+${value}` : String(value);
const time = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC";

function Values({ items }: { items: Array<[string, string | number]> }) {
  return <dl className="ledger-values detail-values">{items.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export function OpportunityDetailView({ id }: { id: string }) {
  const [data, setData] = useState<OpportunityDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.opportunityDetail(id, controller.signal).then((value) => {
      if (!controller.signal.aborted) { setData(value); setError(null); }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause); });
    return () => controller.abort();
  }, [id]);

  if (error) {
    const notFound = error instanceof ModelFCApiError && error.code === "OPPORTUNITY_NOT_FOUND";
    return <div className="workspace"><Link className="text-button" href="/predictions">← Predictions</Link>
      <section className="panel ledger-message" role="alert"><h1>{notFound ? "Opportunity not found" : "Opportunity evidence unavailable"}</h1>
        <p>{notFound ? "This opportunity ID has no recorded event." : describeApiError(error).message}</p></section></div>;
  }
  if (!data) return <div className="workspace" role="status">Loading opportunity evidence…</div>;
  const forecast = data.forecast;
  const context = forecast.historical_context;
  return <div className="workspace opportunity-detail">
    <Link className="text-button" href="/predictions">← Predictions</Link>
    <header className="page-heading"><div><p className="eyebrow">Prospective evidence · {data.competition}</p>
      <h1>{data.home_team} vs {data.away_team}</h1><p>Kickoff {time(data.kickoff_utc)} · {data.settlement_status.replaceAll("_", " ")}</p></div></header>
    <section className="panel detail-panel"><div className="section-title"><span>01</span><div><h2>Opportunity</h2><p>Recorded before kickoff at {data.bookmaker}</p></div></div>
      <h3 className="detail-hero">{data.team} {data.direction} {data.line} · {odds(data.american_odds)}</h3>
      <Values items={[["Zeno decisive", percent(data.model_decisive_probability)], ["Market no-vig", percent(data.no_vig_market_probability)],
        ["Edge", `${(data.no_vig_probability_edge * 100).toFixed(1)} pp`], ["Expected team corners", amount(forecast.expected_team_corners)]]} />
      <p className="ledger-meta">Observation {time(data.recorded_market[0].retrieved_at_utc)} · Qualified {time(data.qualified_at_utc)}</p>
    </section>
    <section className="panel detail-panel"><div className="section-title"><span>02</span><div><h2>Why it qualified</h2><p>Frozen policy · {data.policy_version}</p></div></div>
      <Values items={[["Edge check", `${data.qualification.edge_pass ? "PASS" : "FAIL"} · ${(data.no_vig_probability_edge * 100).toFixed(1)} vs ${(data.qualification.minimum_no_vig_edge * 100).toFixed(1)} pp`],
        ["Price check", `${data.qualification.price_pass ? "PASS" : "FAIL"} · ${odds(data.american_odds)} vs min ${odds(data.qualification.minimum_american_odds)}`],
        ["Market", `${data.qualification.market_type} · ${data.qualification.bookmaker}`]]} />
    </section>
    <section className="panel detail-panel"><div className="section-title"><span>03</span><div><h2>Zeno forecast</h2><p>{forecast.model_name} / {forecast.model_version}</p></div></div>
      <Values items={[["Expected home", amount(forecast.expected_home_corners)], ["Expected away", amount(forecast.expected_away_corners)],
        ["Expected match", amount(forecast.expected_match_corners)], ["Win probability", percent(forecast.model_probability)],
        ["Push probability", percent(forecast.push_probability)], ["Decisive probability", percent(forecast.decisive_model_probability)]]} />
      {context ? <div className="detail-context"><h3>Frozen historical context</h3><Values items={[["Earlier team observations", context.earlier_team_observations],
        ["Home team observations", context.home_team_observations], ["Home venue observations", context.home_venue_observations],
        ["Away team observations", context.away_team_observations], ["Away venue observations", context.away_venue_observations],
        ["History eligibility minimum", context.min_history], ["Venue eligibility minimum", context.min_venue_history]]} /></div>
        : <p className="ledger-meta">Historical counts were not frozen for this legacy prediction.</p>}
      <p className="ledger-meta">History cutoff {forecast.latest_history_date} · Prediction {time(forecast.created_at_utc)} · Target first materialized {time(forecast.materialized_at_utc)}</p>
    </section>
    <section className="panel detail-panel"><div className="section-title"><span>04</span><div><h2>Market at qualification</h2><p>Same observation, bookmaker, team and line</p></div></div>
      <div className="detail-market">{data.market_at_qualification.map((item) => <div className="detail-quote" key={item.direction}>
        <strong>{item.direction}{item.qualified ? " · Qualified" : ""}</strong>
        <Values items={[["American", odds(item.american_odds)], ["Decimal", amount(item.decimal_odds)],
          ["Raw implied", percent(item.implied_probability)], ["No-vig", percent(item.no_vig_probability)]]} />
      </div>)}</div>
    </section>
    <section className="panel detail-panel"><div className="section-title"><span>05</span><div><h2>Recorded market</h2><p>Recorded pre-kickoff snapshots, not live or closing odds</p></div></div>
      <ol className="detail-timeline">{data.recorded_market.map((item) => <li key={item.observation_id}>
        <span>{time(item.retrieved_at_utc)}</span><strong>{item.bookmaker} · {item.direction} {item.line} · {odds(item.american_odds)} ({amount(item.decimal_odds)})</strong>
        {item.qualifying_observation && <small>Qualifying observation</small>}</li>)}</ol>
      {data.recorded_market_count > data.recorded_market.length && <p className="ledger-meta">Showing {data.recorded_market.length} of {data.recorded_market_count} comparable snapshots.</p>}
    </section>
    <section className="panel detail-panel"><div className="section-title"><span>06</span><div><h2>Result</h2><p>{data.result ? `Validated ${data.result}` : "Awaiting validated settlement"}</p></div></div>
      {data.result ? <Values items={[["Home corners", data.actual_home_corners!], ["Away corners", data.actual_away_corners!],
        ["Selected team corners", data.actual_team_corners!], ["Outcome", data.result], ["Realized units", amount(data.realized_profit_units!)]]} />
        : <p className="ledger-meta">No validated result yet. Actual corners and realized units remain undefined.</p>}
      {data.outcome_recorded_at_utc && <p className="ledger-meta">Outcome recorded {time(data.outcome_recorded_at_utc)}</p>}
    </section>
    <section className="panel detail-panel"><div className="section-title"><span>07</span><div><h2>Evidence</h2><p>Immutable record identifiers</p></div></div>
      <Values items={[["Opportunity", data.opportunity_id], ["Prediction", data.prediction_id],
        ["Target", data.target_id], ["Source observation", data.source_observation_id], ["Qualifying observation", data.observation_id],
        ["Provider fixture", data.provider_fixture_id]]} />
    </section>
  </div>;
}
