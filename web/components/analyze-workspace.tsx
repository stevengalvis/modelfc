"use client";

import { useEffect, useMemo, useState } from "react";
import { AnalysisResults } from "./analysis-results";
import { MarketEditor, type EditableMarket } from "./market-editor";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import type { AnalysisResponse, CapabilitiesResponse, FixtureInput, MarketInput } from "@/lib/api/types";

const defaultFixture: FixtureInput = {
  competition: "SP1",
  date: "2026-09-20",
  home_team: "Athletic Club",
  away_team: "Valencia",
};

function newMarket(overrides: Partial<EditableMarket> = {}): EditableMarket {
  return {
    client_market_id: crypto.randomUUID(),
    market_type: "TEAM_TOTAL",
    team_side: "HOME",
    side: "OVER",
    line: "4.5",
    american_odds: "-110",
    ...overrides,
  };
}

const initialMarkets: EditableMarket[] = [
  newMarket({ line: "4.5", american_odds: "-145" }),
  newMarket({ line: "5.5", american_odds: "+105" }),
  newMarket({ team_side: "AWAY", line: "3.5", american_odds: "-120" }),
  newMarket({ market_type: "MATCH_TOTAL", team_side: null, line: "9.5", american_odds: "-110" }),
];

function parseMarket(row: EditableMarket): MarketInput | null {
  const line = Number(row.line);
  const odds = Number(row.american_odds);
  if (!Number.isFinite(line) || line < 0 || !Number.isInteger(line * 2)) return null;
  if (!Number.isInteger(odds) || (odds > -100 && odds < 100)) return null;
  return {
    client_market_id: row.client_market_id,
    market_type: row.market_type,
    team_side: row.market_type === "MATCH_TOTAL" ? null : row.team_side,
    side: row.side,
    line,
    american_odds: odds,
  };
}

export function AnalyzeWorkspace() {
  const [fixture, setFixture] = useState(defaultFixture);
  const [markets, setMarkets] = useState(initialMarkets);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [model, setModel] = useState("venue-opponent-negative-binomial");
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.capabilities()
      .then((response) => {
        setCapabilities(response);
        setModel(response.models[0]);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "Could not load Model FC capabilities.");
      });
  }, []);

  const parsedMarkets = useMemo(() => markets.map(parseMarket), [markets]);
  const formValid = Boolean(
    fixture.date && fixture.home_team.trim() && fixture.away_team.trim() &&
    fixture.home_team.trim().toLowerCase() !== fixture.away_team.trim().toLowerCase() &&
    markets.length > 0 && parsedMarkets.every(Boolean),
  );

  async function analyze() {
    if (!formValid) {
      setError("Complete the fixture and fix invalid lines or American odds before analyzing.");
      return;
    }
    setBusy(true);
    setError(null);
    setAnalysis(null);
    try {
      const response = await api.analyze({
        idempotency_key: crypto.randomUUID(),
        fixture: {
          ...fixture,
          home_team: fixture.home_team.trim(),
          away_team: fixture.away_team.trim(),
        },
        model,
        markets: parsedMarkets as MarketInput[],
      });
      setAnalysis(response);
    } catch (cause) {
      const message = cause instanceof ModelFCApiError
        ? `${cause.code}: ${cause.message}`
        : "Analysis failed. Check the API connection and try again.";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Pre-match workspace</p>
          <h1>Compare the market to the model.</h1>
          <p>One fixture, every available corner line, one decision surface.</p>
        </div>
        <div className="data-chip">
          <span>Latest result data</span>
          <strong>{capabilities?.competitions.find((item) => item.code === fixture.competition)?.latest_result_date ?? "Loading"}</strong>
        </div>
      </section>

      <section className="panel setup-panel" aria-labelledby="fixture-title">
        <div className="section-title">
          <span>01</span>
          <div><h2 id="fixture-title">Fixture</h2><p>Set this once for every market below.</p></div>
        </div>
        <div className="fixture-grid">
          <label>Competition
            <select value={fixture.competition} onChange={(event) => setFixture({ ...fixture, competition: event.target.value })}>
              {(capabilities?.competitions ?? []).map((competition) => (
                <option key={competition.code} value={competition.code}>{competition.name} · {competition.code}</option>
              ))}
            </select>
          </label>
          <label>Date
            <input type="date" value={fixture.date} onChange={(event) => setFixture({ ...fixture, date: event.target.value })} />
          </label>
          <label>Home team
            <input value={fixture.home_team} onChange={(event) => setFixture({ ...fixture, home_team: event.target.value })} />
          </label>
          <label>Away team
            <input value={fixture.away_team} onChange={(event) => setFixture({ ...fixture, away_team: event.target.value })} />
          </label>
        </div>
      </section>

      <section className="panel" aria-labelledby="markets-title">
        <div className="section-title section-title-row">
          <div className="title-cluster"><span>02</span><div><h2 id="markets-title">Available markets</h2><p>Add every line you want to compare.</p></div></div>
          <button className="secondary-button" type="button" onClick={() => setMarkets([...markets, newMarket()])}>+ Add market</button>
        </div>
        <MarketEditor markets={markets} setMarkets={setMarkets} />
        <div className="analyze-bar">
          <label>Model
            <select value={model} onChange={(event) => setModel(event.target.value)}>
              {(capabilities?.models ?? [model]).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <div>
            <span className="market-count">{markets.length} market{markets.length === 1 ? "" : "s"} ready</span>
            <button className="primary-button" type="button" disabled={busy || !capabilities} onClick={analyze}>
              {busy ? "Running model…" : "Analyze all"}
            </button>
          </div>
        </div>
      </section>

      {error && <div className="error-banner" role="alert"><strong>Could not analyze</strong><span>{error}</span></div>}
      {analysis && <AnalysisResults analysis={analysis} />}
    </div>
  );
}
