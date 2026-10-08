"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api/client";
import { BTTS_COMPETITIONS, isBttsCompetition } from "@/lib/api/btts-research";
import type { BttsCompetition, BttsResearchComparison, BttsResearchValue } from "@/lib/api/types";

const READ_REFRESH_INTERVAL_MS = 60_000;
const BOOKMAKERS = { draftkings: "DraftKings", fanduel: "FanDuel" } as const;
const STATUS_LABELS = {
  AVAILABLE: "Current price", STALE: "Stale observation", UNAVAILABLE: "Unavailable",
  UNKNOWN: "Current status unknown", SUPERSEDED: "Superseded observation",
  KICKED_OFF: "Historical · kicked off", FUTURE_OBSERVATION: "Future-dated observation",
} as const;

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const signed = (value: number, digits: number) => `${value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(digits)}`;
const odds = (value: number) => `${value > 0 ? "+" : ""}${value}`;
const money = (value: number) => `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(2)} / $1`;
const utc = (value: string) => new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
}).format(new Date(value)) + " UTC";

function SideComparison({ label, value, best }: {
  label: "BTTS YES" | "BTTS NO"; value: BttsResearchValue; best: boolean;
}) {
  return <section className="btts-side" aria-label={label}>
    <div className="btts-side-heading"><h3>{label}</h3>
      {best ? <span className="btts-best">Best current price</span> : null}
    </div>
    <strong className="btts-price">{odds(value.american_odds)}</strong>
    <dl className="btts-metrics">
      <div><dt>Model</dt><dd>{percent(value.model_probability)}</dd></div>
      <div><dt>No-vig market</dt><dd>{percent(value.no_vig_market_probability)}</dd></div>
      <div><dt>Difference</dt><dd className={value.model_minus_market_difference >= 0 ? "positive" : "negative"}>
        {signed(value.model_minus_market_difference * 100, 1)} pp</dd></div>
      <div><dt>EV per $1 risked</dt><dd className={value.expected_profit >= 0 ? "positive" : "negative"}>
        {money(value.expected_profit)}</dd></div>
    </dl>
  </section>;
}

function ComparisonCard({ item }: { item: BttsResearchComparison }) {
  const available = item.current_status === "AVAILABLE";
  return <article className={`btts-comparison ${available ? "current" : "historical"}`}>
    <header className="btts-book-head">
      <div><h2>{BOOKMAKERS[item.bookmaker]}</h2><span>Paired full-match market</span></div>
      <span className={`btts-status ${available ? "available" : "historical"}`}>{STATUS_LABELS[item.current_status]}</span>
    </header>
    {!available ? <p className="btts-history-warning">Research history only. This price is not presented as currently available.</p> : null}
    <div className="btts-sides">
      <SideComparison label="BTTS YES" value={item.yes} best={item.best_yes_price} />
      <SideComparison label="BTTS NO" value={item.no} best={item.best_no_price} />
    </div>
    <footer className="btts-provenance">
      <p>Observed <time dateTime={item.observation_timestamp_utc}>{utc(item.observation_timestamp_utc)}</time>
        {item.observation_age_seconds === null ? " · Age unavailable" : ` · Age at read ${Math.round(item.observation_age_seconds)}s`}</p>
      <p>Model {item.model_name} · {item.model_version} · Research only</p>
    </footer>
  </article>;
}

interface FixtureGroup { key: string; identity: string; first: BttsResearchComparison; comparisons: BttsResearchComparison[] }

export function BttsResearchDashboard() {
  const [competition, setCompetition] = useState<BttsCompetition>(BTTS_COMPETITIONS[0].code);
  const [items, setItems] = useState<BttsResearchComparison[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let refreshTimer: number | undefined;
    const refresh = () => {
      if (controller.signal.aborted) return;
      setLoading(true);
      setFailed(false);
      setAttempt((value) => value + 1);
    };
    window.addEventListener("focus", refresh);
    api.bttsResearch(competition, controller.signal).then((value) => {
      if (!controller.signal.aborted) setItems(value);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    }).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false);
        refreshTimer = window.setTimeout(refresh, READ_REFRESH_INTERVAL_MS);
      }
    });
    return () => {
      controller.abort();
      window.clearTimeout(refreshTimer);
      window.removeEventListener("focus", refresh);
    };
  }, [competition, attempt]);

  const groups = useMemo(() => {
    const result: FixtureGroup[] = [];
    for (const item of items) {
      const key = `${item.competition}\0${item.fixture.provider}\0${item.fixture.provider_fixture_id}`;
      const previous = result.at(-1);
      if (previous?.identity === key) previous.comparisons.push(item);
      else result.push({ key: `${key}\0${item.comparison_id}`, identity: key, first: item, comparisons: [item] });
    }
    return result;
  }, [items]);

  return <div className="workspace btts-workspace">
    <header className="page-heading btts-page-heading"><div><span className="eyebrow">Research</span>
      <h1>BTTS Value</h1>
      <p>Compare Zeno&apos;s experimental BTTS probabilities with sportsbook prices.</p></div>
      <label className="btts-competition">League
        <select aria-label="League" value={competition} onChange={(event) => {
          if (isBttsCompetition(event.target.value) && event.target.value !== competition) {
            setItems([]);
            setLoading(true);
            setFailed(false);
            setCompetition(event.target.value);
          }
        }}>
          {BTTS_COMPETITIONS.map((item) => <option key={item.code} value={item.code}>{item.name} ({item.code})</option>)}
        </select>
      </label>
    </header>
    <p className="btts-research-notice"><strong>Research only.</strong> Comparisons are experimental evidence, not production-qualified recommendations.</p>
    {loading ? <section className="panel ledger-message" role="status">Loading BTTS research…</section>
      : failed ? <section className="panel ledger-message" role="alert">
        <h2>BTTS research unavailable</h2>
        <p>Could not load research comparisons. Please try again.</p>
        <button className="secondary-button" onClick={() => {
          setLoading(true); setFailed(false); setAttempt((value) => value + 1);
        }}>Retry</button>
      </section>
      : groups.length === 0 ? <section className="panel ledger-message">
        <h2>No BTTS research comparisons yet</h2>
        <p>Comparisons will appear when Zeno captures eligible BTTS market observations.</p>
      </section>
      : <div className="btts-fixtures" aria-label="BTTS comparisons in backend order">
        {groups.map(({ key, first, comparisons }) => <section className="panel btts-fixture" key={key}>
          <header className="btts-fixture-head">
            <div><span className="eyebrow">{BTTS_COMPETITIONS.find((item) => item.code === first.competition)?.name} · {first.competition}</span>
              <h2>{first.fixture.home_team} <span>vs</span> {first.fixture.away_team}</h2>
              <p>Kickoff <time dateTime={first.fixture.kickoff_utc}>{utc(first.fixture.kickoff_utc)}</time></p></div>
            <span className="btts-research-chip">Research</span>
          </header>
          <div className="btts-comparison-list">
            {comparisons.map((item) => <ComparisonCard item={item} key={item.comparison_id} />)}
          </div>
        </section>)}
      </div>}
  </div>;
}
