"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnalysisResults } from "./analysis-results";
import { MarketEditor } from "./market-editor";
import { CapabilityFeedback } from "./capability-feedback";
import { api, apiMode } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { eligibleTeams, findCompetition, unavailableMarketReason } from "@/lib/api/capabilities";
import { requestFingerprint, validateAnalysisInput } from "@/lib/analysis-input";
import { competitionLabels, parseSportsbookInput, type EditableFixtureInput, type ParsedSportsbookInput } from "@/lib/parse-sportsbook-input";
import type { AnalysisResponse, CapabilitiesResponse } from "@/lib/api/types";

const exampleInput = `Championship
2026-09-17
Birmingham vs Millwall
Birmingham team corners O4 -110`;

const mixedExampleInput = `Championship
2026-10-01
Birmingham vs Millwall
Birmingham O4.5 -110
Total O9.5 +105`;

interface RetryState {
  fingerprint: string;
  key: string;
  retryable: boolean;
}

export function AnalyzeWorkspace() {
  const [rawInput, setRawInput] = useState("");
  const [parsed, setParsed] = useState<ParsedSportsbookInput | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState<ReturnType<typeof describeApiError> | null>(null);
  const [capabilitiesAttempt, setCapabilitiesAttempt] = useState(0);
  const [model, setModel] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [analysisKey, setAnalysisKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ReturnType<typeof describeApiError> | null>(null);
  const versionRef = useRef(0);
  const requestRef = useRef<AbortController | null>(null);
  const retryRef = useRef<RetryState | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.capabilities(controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setCapabilities(response);
        setModel(response.models[0] ?? "");
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setCapabilitiesError(describeApiError(cause));
      });
    return () => controller.abort();
  }, [capabilitiesAttempt]);

  useEffect(() => () => requestRef.current?.abort(), []);

  const validation = useMemo(
    () => parsed ? validateAnalysisInput(parsed.fixture, parsed.markets, capabilities) : null,
    [parsed, capabilities],
  );

  function invalidateAnalysis() {
    requestRef.current?.abort();
    requestRef.current = null;
    versionRef.current += 1;
    retryRef.current = null;
    setAnalysis(null);
    setError(null);
    setBusy(false);
  }

  function changeRawInput(value: string) {
    invalidateAnalysis();
    setRawInput(value);
    setParsed(null);
  }

  function parseInput() {
    invalidateAnalysis();
    setParsed(parseSportsbookInput(rawInput));
  }

  function updateFixture(patch: Partial<EditableFixtureInput>) {
    if (!parsed) return;
    invalidateAnalysis();
    setParsed({ ...parsed, fixture: { ...parsed.fixture, ...patch } });
  }

  async function analyze() {
    if (!parsed || !validation?.fixture || !model) return;
    const hasMarketErrors = parsed.markets.some((market) => Object.keys(validation.marketErrors[market.client_market_id] ?? {}).length > 0);
    if (hasMarketErrors || validation.batchError || validation.competitionError || validation.validMarkets.length === 0) {
      setError({ title: "Input validation failed", message: "Correct the highlighted fields before analyzing." });
      return;
    }

    const fingerprint = requestFingerprint(validation.fixture, model, validation.validMarkets);
    const existing = retryRef.current;
    const idempotencyKey = existing?.fingerprint === fingerprint && existing.retryable
      ? existing.key
      : crypto.randomUUID();
    retryRef.current = { fingerprint, key: idempotencyKey, retryable: true };
    const version = versionRef.current;
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setBusy(true);
    setError(null);
    setAnalysis(null);
    try {
      const response = await api.analyze({
        idempotency_key: idempotencyKey,
        fixture: validation.fixture,
        model,
        markets: validation.validMarkets,
      }, controller.signal);
      if (version !== versionRef.current || controller.signal.aborted) return;
      retryRef.current = { fingerprint, key: idempotencyKey, retryable: false };
      setAnalysisKey(idempotencyKey);
      setAnalysis(response);
    } catch (cause) {
      if (controller.signal.aborted || (cause instanceof DOMException && cause.name === "AbortError")) return;
      if (version !== versionRef.current) return;
      setError(describeApiError(cause));
    } finally {
      if (version === versionRef.current && requestRef.current === controller) {
        requestRef.current = null;
        setBusy(false);
      }
    }
  }

  const capability = findCompetition(capabilities, parsed?.fixture.competition ?? "");
  const excludedCount = Object.keys(validation?.marketUnavailable ?? {}).length;
  const unavailableTypes = {
    TEAM_TOTAL: unavailableMarketReason("TEAM_TOTAL", capability, capabilities) ?? undefined,
    MATCH_TOTAL: unavailableMarketReason("MATCH_TOTAL", capability, capabilities) ?? undefined,
  };
  const hasFixtureErrors = Boolean(validation && Object.keys(validation.fixtureErrors).length > 0);
  const hasMarketErrors = Boolean(parsed && validation && parsed.markets.some(
    (market) => Object.keys(validation.marketErrors[market.client_market_id] ?? {}).length > 0,
  ));
  const ready = Boolean(
    capabilities && model && validation?.fixture && validation.validMarkets.length > 0
    && !hasFixtureErrors && !hasMarketErrors && !validation?.batchError && !validation?.competitionError,
  );

  return (
    <div className="workspace">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Pre-match workspace</p>
          <h1>Paste the lines. Find the value.</h1>
          <p>Paste messy corner lines, correct what was parsed, then compare every market in one analysis.</p>
        </div>
        {capability?.latest_result_date ? <div className="data-chip"><span>Latest result data</span><strong>{capability.latest_result_date}</strong></div> : null}
      </section>

      {apiMode === "mock" ? <p className="demo-notice"><strong>Mock data.</strong> Fixed examples from synthetic Birmingham / Millwall history. Other edits require a live API.</p> : null}
      {capabilitiesError ? (
        <div className="error-banner" role="alert">
          <div><strong>{capabilitiesError.title}</strong><p>{capabilitiesError.message} Analysis is disabled.</p></div>
          <button className="secondary-button" type="button" onClick={() => {
            invalidateAnalysis(); setCapabilities(null); setModel(""); setCapabilitiesError(null);
            setCapabilitiesAttempt((attempt) => attempt + 1);
          }}>Retry connection</button>
        </div>
      ) : null}

      <section className="panel paste-panel" aria-labelledby="paste-title">
        <div className="section-title section-title-row">
          <div className="title-cluster"><span>01</span><div><h2 id="paste-title">Paste sportsbook lines</h2><p>League, date, fixture, then one corner market per line.</p></div></div>
          <div className="example-actions">
            <button className="text-button" type="button" onClick={() => changeRawInput(exampleInput)}>Use example</button>
            <button className="text-button" type="button" onClick={() => changeRawInput(mixedExampleInput)}>Mixed board example</button>
          </div>
        </div>
        <div className="paste-input-wrap">
          <textarea aria-label="Sportsbook fixture and corner markets" placeholder={exampleInput} value={rawInput} onChange={(event) => changeRawInput(event.target.value)} />
          <div className="paste-actions">
            <span>One fixture · Full-match corner markets</span>
            <button className="secondary-button" type="button" disabled={!rawInput.trim()} onClick={parseInput}>Parse lines</button>
          </div>
        </div>
      </section>

      {parsed && validation ? (
        <section className="panel review-panel" aria-labelledby="review-title">
          <div className="section-title">
            <span>02</span>
            <div><h2 id="review-title">Review and correct</h2><p>Edit the parsed fields here; the original paste stays intact above.</p></div>
          </div>
          <div className="fixture-editor">
            <label><span>Competition code</span><input aria-label="Competition code" aria-invalid={Boolean(validation.fixtureErrors.competition || validation.competitionError)} list="competition-codes" value={parsed.fixture.competition} onChange={(event) => updateFixture({ competition: event.target.value.toLocaleUpperCase() })} /><small>{validation.fixtureErrors.competition ?? competitionLabels[parsed.fixture.competition] ?? "Use a code reported by the backend"}</small></label>
            <datalist id="competition-codes">{capabilities?.competitions.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</datalist>
            <label><span>Date</span><input aria-invalid={Boolean(validation.fixtureErrors.date)} aria-label="Fixture date" placeholder="YYYY-MM-DD" value={parsed.fixture.date} onChange={(event) => updateFixture({ date: event.target.value })} /><small>{validation.fixtureErrors.date ?? "YYYY-MM-DD"}</small></label>
            <label><span>Home team</span><input aria-label="Home team" list="home-teams" aria-invalid={Boolean(validation.fixtureErrors.home_team)} value={parsed.fixture.home_team} onChange={(event) => updateFixture({ home_team: event.target.value })} /><small>{validation.fixtureErrors.home_team ?? "Canonical names eligible at home"}</small></label>
            <datalist id="home-teams">{eligibleTeams(capability, "HOME").filter((team) => team !== parsed.fixture.away_team.trim()).map((team) => <option key={team} value={team} />)}</datalist>
            <label><span>Away team</span><input aria-label="Away team" list="away-teams" aria-invalid={Boolean(validation.fixtureErrors.away_team)} value={parsed.fixture.away_team} onChange={(event) => updateFixture({ away_team: event.target.value })} /><small>{validation.fixtureErrors.away_team ?? "Canonical names eligible away"}</small></label>
            <datalist id="away-teams">{eligibleTeams(capability, "AWAY").filter((team) => team !== parsed.fixture.home_team.trim()).map((team) => <option key={team} value={team} />)}</datalist>
          </div>

          {validation.competitionError && !capability ? <div className="capability-note blocked" role="alert"><strong>Competition unavailable</strong><span>{validation.competitionError}</span></div> : null}
          {capability ? <CapabilityFeedback competition={capability} /> : null}
          {!capabilities && !capabilitiesError ? <p className="capability-note">Loading backend readiness…</p> : null}
          {capability?.latest_result_date && parsed.fixture.date <= capability.latest_result_date ? <p className="capability-note">Team eligibility uses loaded history. For this earlier fixture date, the API must recheck how much history precedes kickoff.</p> : null}

          <MarketEditor
            markets={parsed.markets}
            errors={validation.marketErrors}
            unavailable={validation.marketUnavailable}
            unavailableTypes={unavailableTypes}
            onChange={(markets) => { invalidateAnalysis(); setParsed({ ...parsed, markets }); }}
          />
          {validation.batchError ? <div className="parse-errors" role="alert"><strong>Batch limit</strong><p>{validation.batchError}</p></div> : null}
          {excludedCount > 0 ? <p className="exclusion-note">{excludedCount} unavailable market{excludedCount === 1 ? "" : "s"} will remain here and will not be sent. Analyze all runs every ready market.</p> : null}
          <div className="analyze-bar simple-analyze-bar">
            <span className="market-count">{validation.validMarkets.length} of {parsed.markets.length} markets ready</span>
            <span className="mode-note">{apiMode === "mock" ? "Fixed backend demo fixture" : "Live API response"}</span>
            <button className="primary-button" type="button" disabled={busy || !ready} onClick={analyze}>{busy ? "Running model…" : "Analyze all"}</button>
          </div>
        </section>
      ) : null}

      {error ? <div className="error-banner" role="alert"><strong>{error.title}</strong><span>{error.message}</span></div> : null}
      {analysis ? <AnalysisResults key={analysisKey} analysis={analysis} /> : null}
    </div>
  );
}
