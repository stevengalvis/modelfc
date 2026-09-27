"use client";

import { useState } from "react";
import type { AnalysisResponse, AnalyzedMarket } from "@/lib/api/types";

function percent(value: number | null, signed = false): string {
  if (value === null) return "—";
  return `${signed && value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function money(value: number | null): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(2)}`;
}

function marketName(market: AnalyzedMarket): string {
  const subject = market.market_type === "MATCH_TOTAL" ? "Match" : market.team;
  return `${subject} ${market.side === "OVER" ? "O" : "U"}${market.line}`;
}

export function AnalysisResults({ analysis }: { analysis: AnalysisResponse }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const supported = analysis.markets.filter((market) => market.status === "SUPPORTED");

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const loggingReason = analysis.pick_logging.status === "DISABLED"
    ? `Backend disabled logging: ${analysis.pick_logging.reason ?? "no reason supplied"}.`
    : "Backend permits logging, but the selection endpoint is not available in this frontend slice yet.";

  return (
    <section className="results-section" aria-labelledby="results-title">
      <div className="results-heading">
        <div>
          <p className="eyebrow">Analysis complete</p>
          <h2 id="results-title">{analysis.fixture.home_team} <span>vs</span> {analysis.fixture.away_team}</h2>
          <p>{analysis.fixture.competition} · {analysis.fixture.date} · {analysis.forecast.model} · {analysis.forecast.model_version}</p>
        </div>
        <div className="forecast-strip">
          <div><span>Home xCorners</span><strong>{analysis.forecast.home_expected_corners.toFixed(2)}</strong></div>
          <div><span>Away xCorners</span><strong>{analysis.forecast.away_expected_corners.toFixed(2)}</strong></div>
          <div><span>Match xCorners</span><strong>{analysis.forecast.match_expected_corners.toFixed(2)}</strong></div>
        </div>
      </div>

      {analysis.warnings.length > 0 ? (
        <div className="analysis-warnings" role="status">
          <strong>Analysis warnings</strong>
          <ul>{analysis.warnings.map((warning) => <li key={`${warning.code}-${warning.message}`}><b>{warning.code}</b> {warning.message}</li>)}</ul>
        </div>
      ) : null}

      <p className="probability-note">On whole-number lines, a push returns the stake. Edge uses the backend’s decisive probability: win chance after pushes are excluded. Raw win and push probabilities remain visible.</p>
      <div className="result-table-wrap">
        <table className="result-table">
          <thead><tr><th aria-label="Select" /><th>Market</th><th>Odds</th><th>Raw win</th><th>Push</th><th>Decisive</th><th>Break-even</th><th>Edge</th><th>EV / $1</th><th>Status</th></tr></thead>
          <tbody>
            {analysis.markets.map((market) => {
              const isSupported = market.status === "SUPPORTED";
              const isSelected = selected.has(market.client_market_id);
              return (
                <tr className={isSelected ? "selected" : ""} key={market.client_market_id}>
                  <td><input aria-label={`Select ${marketName(market)}`} checked={isSelected} disabled={!isSupported} type="checkbox" onChange={() => toggle(market.client_market_id)} /></td>
                  <td data-label="Market">
                    <strong>{marketName(market)}</strong>
                    <small>{market.market_type === "TEAM_TOTAL" ? "Team total" : "Match total"}</small>
                    {market.warnings.map((warning) => <span className="market-warning" key={`${warning.code}-${warning.message}`} title={warning.message}>{warning.code}: {warning.message}</span>)}
                    {!isSupported && market.unsupported_reason ? <span className="market-warning">{market.unsupported_reason}</span> : null}
                  </td>
                  <td data-label="Odds" className="mono">{market.american_odds > 0 ? "+" : ""}{market.american_odds}</td>
                  <td data-label="Raw win" className="mono strong">{percent(market.model_probability)}</td>
                  <td data-label="Push" className="mono">{percent(market.push_probability)}</td>
                  <td data-label="Decisive" className="mono strong">{percent(market.decisive_model_probability)}</td>
                  <td data-label="Break-even" className="mono">{percent(market.implied_probability)}</td>
                  <td data-label="Edge" className={`mono ${market.probability_edge !== null && market.probability_edge > 0 ? "positive" : "negative"}`}>{percent(market.probability_edge, true)}</td>
                  <td data-label="EV / $1" className={`mono ${market.expected_profit !== null && market.expected_profit > 0 ? "positive" : "negative"}`}>{money(market.expected_profit)}</td>
                  <td data-label="Status">{isSupported ? <span className="status supported">Supported</span> : <span className="status unsupported">Unsupported</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="selection-bar">
        <div><strong>{selected.size} selected</strong><span>{supported.length} supported markets · {loggingReason}</span></div>
        <button type="button" className="ghost-button" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>Clear</button>
        <button type="button" className="ghost-button" onClick={() => setSelected(new Set(supported.map((market) => market.client_market_id)))}>Select all supported</button>
        <button type="button" className="primary-button" disabled title={loggingReason}>Log selected ({selected.size})</button>
      </div>
    </section>
  );
}
