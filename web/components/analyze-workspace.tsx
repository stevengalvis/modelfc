"use client";

import { useEffect, useState } from "react";
import { AnalysisResults } from "./analysis-results";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import { formatMarket, parseSportsbookInput, type ParsedSportsbookInput } from "@/lib/parse-sportsbook-input";
import type { AnalysisResponse, CapabilitiesResponse, FixtureInput } from "@/lib/api/types";

const exampleInput = `Championship
2026-09-20
Coventry City vs Birmingham City
Coventry City team corners O4.5 -145
Coventry City team corners O5.5 +105
Birmingham City team corners O3.5 -120
Match total corners U10.5 -125`;

function competitionName(code: string | undefined): string {
  return code === "SP2" ? "La Liga 2" : code === "E1" ? "Championship" : "Unknown";
}

function completeFixture(parsed: ParsedSportsbookInput | null): FixtureInput | null {
  const fixture = parsed?.fixture;
  if (!fixture?.competition || !fixture.date || !fixture.home_team || !fixture.away_team) return null;
  return fixture as FixtureInput;
}

export function AnalyzeWorkspace() {
  const [rawInput, setRawInput] = useState("");
  const [parsed, setParsed] = useState<ParsedSportsbookInput | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [model, setModel] = useState("");
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

  function parseInput() {
    const result = parseSportsbookInput(rawInput);
    setParsed(result);
    setAnalysis(null);
    setError(null);
  }

  async function analyze() {
    const fixture = completeFixture(parsed);
    if (!fixture || !parsed || parsed.errors.length > 0 || parsed.markets.length === 0) {
      setError("Fix the highlighted input before analyzing.");
      return;
    }
    setBusy(true);
    setError(null);
    setAnalysis(null);
    try {
      const response = await api.analyze({
        idempotency_key: crypto.randomUUID(),
        fixture,
        model,
        markets: parsed.markets,
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

  const fixture = completeFixture(parsed);
  const ready = Boolean(capabilities && model && fixture && parsed && parsed.markets.length > 0 && parsed.errors.length === 0);
  const latestResultDate = fixture
    ? capabilities?.competitions.find((item) => item.code === fixture.competition)?.latest_result_date
    : null;

  return (
    <div className="workspace">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Pre-match workspace</p>
          <h1>Paste the lines. Find the value.</h1>
          <p>Model FC turns one fixture and every available corner line into a comparable decision table.</p>
        </div>
        {latestResultDate ? <div className="data-chip"><span>Latest result data</span><strong>{latestResultDate}</strong></div> : null}
      </section>

      <section className="panel paste-panel" aria-labelledby="paste-title">
        <div className="section-title section-title-row">
          <div className="title-cluster"><span>01</span><div><h2 id="paste-title">Paste sportsbook lines</h2><p>Include the league, date, fixture, and every corner market you want to compare.</p></div></div>
          <button className="text-button" type="button" onClick={() => { setRawInput(exampleInput); setParsed(null); setAnalysis(null); }}>Use example</button>
        </div>
        <div className="paste-input-wrap">
          <textarea
            aria-label="Sportsbook fixture and corner markets"
            placeholder={exampleInput}
            value={rawInput}
            onChange={(event) => { setRawInput(event.target.value); setParsed(null); setAnalysis(null); }}
          />
          <div className="paste-actions">
            <span>Championship and La Liga 2 only</span>
            <button className="secondary-button" type="button" disabled={!rawInput.trim()} onClick={parseInput}>Parse lines</button>
          </div>
        </div>
      </section>

      {parsed ? (
        <section className="panel review-panel" aria-labelledby="review-title">
          <div className="section-title">
            <span>02</span>
            <div><h2 id="review-title">Review parsed markets</h2><p>Confirm what Model FC found before running the model.</p></div>
          </div>
          {fixture ? (
            <div className="fixture-summary">
              <div><span>Competition</span><strong>{competitionName(fixture.competition)}</strong></div>
              <div><span>Fixture</span><strong>{fixture.home_team} <em>vs</em> {fixture.away_team}</strong></div>
              <div><span>Date</span><strong>{fixture.date}</strong></div>
            </div>
          ) : null}
          {parsed.markets.length > 0 ? (
            <div className="parsed-markets">
              {parsed.markets.map((item, index) => (
                <div key={item.client_market_id}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{formatMarket(item, parsed.fixture)}</strong>
                  <small>{item.market_type === "MATCH_TOTAL" ? "Match total" : "Team total"}</small>
                </div>
              ))}
            </div>
          ) : null}
          {parsed.errors.length > 0 ? (
            <div className="parse-errors" role="alert">
              <strong>Some text needs attention</strong>
              <ul>{parsed.errors.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          ) : null}
          <div className="analyze-bar simple-analyze-bar">
            <span className="market-count">{parsed.markets.length} market{parsed.markets.length === 1 ? "" : "s"} parsed</span>
            <button className="primary-button" type="button" disabled={busy || !ready} onClick={analyze}>
              {busy ? "Running model…" : "Analyze all"}
            </button>
          </div>
        </section>
      ) : null}

      {error ? <div className="error-banner" role="alert"><strong>Could not analyze</strong><span>{error}</span></div> : null}
      {analysis ? <AnalysisResults analysis={analysis} /> : null}
    </div>
  );
}
