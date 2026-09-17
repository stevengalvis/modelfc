"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnalysisResults } from "./analysis-results";
import { MarketEditor } from "./market-editor";
import { CapabilityFeedback } from "./capability-feedback";
import { api, apiMode } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { eligibleTeams, findCompetition, unavailableMarketReason } from "@/lib/api/capabilities";
import { MAX_MARKETS_PER_ANALYSIS, requestFingerprint, validateAnalysisBlocks, type BlockValidation } from "@/lib/analysis-input";
import { competitionLabels, parseSportsbookInput, type EditableFixtureInput, type ParsedInputBlock, type ParsedSportsbookInput } from "@/lib/parse-sportsbook-input";
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

interface AnalysisEntry {
  blockId: string;
  response: AnalysisResponse;
}

function hasBlockingMarketErrors(validation: BlockValidation): boolean {
  return Object.entries(validation.marketErrors).some(([id, errors]) =>
    !validation.marketUnavailable[id] && Object.keys(errors).length > 0);
}

export function AnalyzeWorkspace() {
  const [rawInput, setRawInput] = useState("");
  const [parsed, setParsed] = useState<ParsedSportsbookInput | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState<ReturnType<typeof describeApiError> | null>(null);
  const [capabilitiesAttempt, setCapabilitiesAttempt] = useState(0);
  const [model, setModel] = useState("");
  const [analyses, setAnalyses] = useState<AnalysisEntry[]>([]);
  const [analysisKey, setAnalysisKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ReturnType<typeof describeApiError> | null>(null);
  const versionRef = useRef(0);
  const requestRef = useRef<AbortController | null>(null);
  const retryRef = useRef<Map<string, RetryState>>(new Map());

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

  const validations = useMemo(
    () => parsed ? validateAnalysisBlocks(parsed.blocks, capabilities) : [],
    [parsed, capabilities],
  );

  function invalidateAnalysis() {
    requestRef.current?.abort();
    requestRef.current = null;
    versionRef.current += 1;
    retryRef.current.clear();
    setAnalyses([]);
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

  function updateBlock(blockId: string, update: (block: ParsedInputBlock) => ParsedInputBlock) {
    if (!parsed) return;
    invalidateAnalysis();
    setParsed({ ...parsed, blocks: parsed.blocks.map((block) => block.block_id === blockId ? update(block) : block) });
  }

  function updateFixture(blockId: string, patch: Partial<EditableFixtureInput>) {
    updateBlock(blockId, (block) => ({ ...block, fixture: { ...block.fixture, ...patch } }));
  }

  async function analyze() {
    if (!parsed || !model) return;
    const readyBlocks = parsed.blocks.map((block, index) => ({ block, validation: validations[index] }))
      .filter(({ validation }) => Boolean(validation?.fixture && validation.validMarkets.length > 0
        && Object.keys(validation.fixtureErrors).length === 0
        && validation.validMarkets.every((market) => Object.keys(validation.marketErrors[market.client_market_id] ?? {}).length === 0)
        && !hasBlockingMarketErrors(validation)
        && !validation.batchError && !validation.competitionError));
    if (readyBlocks.length === 0) {
      setError({ title: "Input validation failed", message: "Correct at least one complete fixture and its supported markets before analyzing." });
      return;
    }

    const version = versionRef.current;
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setBusy(true);
    setError(null);
    setAnalyses([]);
    const requests = readyBlocks.map(({ block, validation }) => {
      const fixture = validation.fixture!;
      const fingerprint = requestFingerprint(fixture, model, validation.validMarkets);
      const existing = retryRef.current.get(block.block_id);
      const idempotencyKey = existing?.fingerprint === fingerprint && existing.retryable
        ? existing.key
        : crypto.randomUUID();
      retryRef.current.set(block.block_id, { fingerprint, key: idempotencyKey, retryable: true });
      return { blockId: block.block_id, fingerprint, idempotencyKey, fixture, markets: validation.validMarkets };
    });
    try {
      const results = await Promise.allSettled(requests.map((request) => api.analyze({
        idempotency_key: request.idempotencyKey,
        fixture: request.fixture,
        model,
        markets: request.markets,
      }, controller.signal)));
      if (version !== versionRef.current || controller.signal.aborted) return;
      const successes: AnalysisEntry[] = [];
      let firstError: ReturnType<typeof describeApiError> | null = null;
      results.forEach((result, index) => {
        const request = requests[index];
        if (result.status === "fulfilled") {
          retryRef.current.set(request.blockId, { fingerprint: request.fingerprint, key: request.idempotencyKey, retryable: false });
          successes.push({ blockId: request.blockId, response: result.value });
        } else if (!firstError) {
          firstError = describeApiError(result.reason);
        }
      });
      setAnalysisKey(requests.map((request) => request.idempotencyKey).join(":"));
      setAnalyses(successes);
      setError(firstError);
    } finally {
      if (version === versionRef.current && requestRef.current === controller) {
        requestRef.current = null;
        setBusy(false);
      }
    }
  }

  const parsedBlocks = parsed?.blocks ?? [];
  const readyCount = validations.filter((validation) => validation.fixture
    && validation.validMarkets.length > 0
    && Object.keys(validation.fixtureErrors).length === 0
    && !hasBlockingMarketErrors(validation)
    && !validation.batchError && !validation.competitionError
    && validation.validMarkets.every((market) => Object.keys(validation.marketErrors[market.client_market_id] ?? {}).length === 0)).length;
  const readyMarketCount = validations.reduce((total, validation) => total + Math.min(validation.validMarkets.length, MAX_MARKETS_PER_ANALYSIS), 0);
  const marketCountLabel = parsedBlocks.length === 1
    ? `${readyMarketCount} of ${parsedBlocks[0].markets.length} markets ready`
    : `${readyCount} fixture${readyCount === 1 ? "" : "s"} ready · ${readyMarketCount} markets ready`;
  const primaryCapability = findCompetition(capabilities, parsedBlocks[0]?.fixture.competition ?? "");

  return (
    <div className="workspace">
      <section className="page-heading">
        <div>
          <p className="eyebrow">Pre-match workspace</p>
          <h1>Paste the lines. Find the value.</h1>
          <p>Paste messy corner lines, correct what was parsed, then compare every market in one analysis.</p>
        </div>
        {primaryCapability?.latest_result_date ? <div className="data-chip"><span>Latest result data</span><strong>{primaryCapability.latest_result_date}</strong></div> : null}
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
          <div className="title-cluster"><span>01</span><div><h2 id="paste-title">Paste sportsbook lines</h2><p>Competition code or label, date, fixture, then one corner market per line. Separate fixtures with a blank line.</p></div></div>
          <div className="example-actions">
            <button className="text-button" type="button" onClick={() => changeRawInput(exampleInput)}>Use example</button>
            <button className="text-button" type="button" onClick={() => changeRawInput(mixedExampleInput)}>Mixed board example</button>
          </div>
        </div>
        <div className="paste-input-wrap">
          <textarea aria-label="Sportsbook fixture and corner markets" placeholder={exampleInput} value={rawInput} onChange={(event) => changeRawInput(event.target.value)} />
          <div className="paste-actions">
            <span>One or more fixtures · Full-match corner markets</span>
            <button className="secondary-button" type="button" disabled={!rawInput.trim()} onClick={parseInput}>Parse lines</button>
          </div>
        </div>
      </section>

      {parsed ? (
        <section className="panel review-panel" aria-labelledby="review-title">
          <div className="section-title">
            <span>02</span>
            <div><h2 id="review-title">Review and correct</h2><p>Edit parsed fields; each block keeps its original source and warnings.</p></div>
          </div>
          {parsedBlocks.map((block, index) => {
            const validation: BlockValidation = validations[index];
            const capability = findCompetition(capabilities, block.fixture.competition);
            const excludedCount = Object.keys(validation?.marketUnavailable ?? {}).length;
            const unavailableTypes = {
              TEAM_TOTAL: unavailableMarketReason("TEAM_TOTAL", capability, capabilities) ?? undefined,
              MATCH_TOTAL: unavailableMarketReason("MATCH_TOTAL", capability, capabilities) ?? undefined,
            };
            const hasMarketErrors = block.markets.some((market) => !validation?.marketUnavailable[market.client_market_id]
              && Object.keys(validation?.marketErrors[market.client_market_id] ?? {}).length > 0);
            return (
              <div className="input-block" key={block.block_id}>
                <div className="block-heading"><strong>Fixture {index + 1}</strong><details><summary>Original source</summary><pre>{block.source_text || "(empty block)"}</pre></details></div>
                {block.warnings.map((warning) => <p className="capability-note" role="status" key={warning}>{warning}</p>)}
                <div className="fixture-editor">
                  <label><span>Competition code</span><input aria-label="Competition code" aria-invalid={Boolean(validation.fixtureErrors.competition || validation.competitionError)} list={`competition-codes-${block.block_id}`} value={block.fixture.competition} onChange={(event) => updateFixture(block.block_id, { competition: event.target.value.toLocaleUpperCase() })} /><small>{validation.fixtureErrors.competition ?? competitionLabels[block.fixture.competition] ?? "Use a code reported by the backend"}</small></label>
                  <datalist id={`competition-codes-${block.block_id}`}>{capabilities?.competitions.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</datalist>
                  <label><span>Date</span><input aria-invalid={Boolean(validation.fixtureErrors.date)} aria-label="Fixture date" placeholder="YYYY-MM-DD" value={block.fixture.date} onChange={(event) => updateFixture(block.block_id, { date: event.target.value })} /><small>{validation.fixtureErrors.date ?? "YYYY-MM-DD"}</small></label>
                  <label><span>Home team</span><input aria-label="Home team" list={`home-teams-${block.block_id}`} aria-invalid={Boolean(validation.fixtureErrors.home_team)} value={block.fixture.home_team} onChange={(event) => updateFixture(block.block_id, { home_team: event.target.value })} /><small>{validation.fixtureErrors.home_team ?? "Canonical names eligible at home"}</small></label>
                  <datalist id={`home-teams-${block.block_id}`}>{eligibleTeams(capability, "HOME").filter((team) => team !== block.fixture.away_team.trim()).map((team) => <option key={team} value={team} />)}</datalist>
                  <label><span>Away team</span><input aria-label="Away team" list={`away-teams-${block.block_id}`} aria-invalid={Boolean(validation.fixtureErrors.away_team)} value={block.fixture.away_team} onChange={(event) => updateFixture(block.block_id, { away_team: event.target.value })} /><small>{validation.fixtureErrors.away_team ?? "Canonical names eligible away"}</small></label>
                  <datalist id={`away-teams-${block.block_id}`}>{eligibleTeams(capability, "AWAY").filter((team) => team !== block.fixture.home_team.trim()).map((team) => <option key={team} value={team} />)}</datalist>
                </div>
                {validation.competitionError && !capability ? <div className="capability-note blocked" role="alert"><strong>Competition unavailable</strong><span>{validation.competitionError}</span></div> : null}
                {capability ? <CapabilityFeedback competition={capability} /> : null}
                {!capabilities && !capabilitiesError ? <p className="capability-note">Loading backend readiness…</p> : null}
                <MarketEditor markets={block.markets} errors={validation.marketErrors} unavailable={validation.marketUnavailable} unavailableTypes={unavailableTypes} onChange={(markets) => updateBlock(block.block_id, (current) => ({ ...current, markets }))} />
                {validation.batchError ? <div className="parse-errors" role="alert"><strong>Batch limit</strong><p>{validation.batchError}</p></div> : null}
                {excludedCount > 0 ? <p className="exclusion-note">{excludedCount} unavailable market{excludedCount === 1 ? "" : "s"} will remain visible and will not be sent.</p> : null}
                {hasMarketErrors ? <p className="capability-note blocked">Correct unresolved market fields before this fixture can be analyzed.</p> : null}
              </div>
            );
          })}
          <div className="analyze-bar simple-analyze-bar">
            <span className="market-count">{marketCountLabel}</span>
            <span className="mode-note">{apiMode === "mock" ? "Fixed backend demo fixture" : apiMode === "live" ? "Live API response" : "API mode not configured"}</span>
            <button className="primary-button" type="button" disabled={busy || readyCount === 0} onClick={analyze}>{busy ? "Running model…" : "Analyze all"}</button>
          </div>
        </section>
      ) : null}

      {error ? <div className="error-banner" role="alert"><strong>{error.title}</strong><span>{error.message}</span></div> : null}
      {analyses.map(({ blockId, response }) => <AnalysisResults key={`${analysisKey}:${blockId}`} analysis={response} />)}
    </div>
  );
}
