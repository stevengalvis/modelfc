import { isApiErrorBody, ModelFCApiError } from "./errors";
import { mockAnalyze, mockCapabilities, mockPredictions, mockOpportunities, mockPerformance } from "./mock";
import { decodePredictions, decodeOpportunities, decodePerformance } from "./prospective";
import { decodeCapabilities } from "./capabilities";
import type { AnalysisRequest, AnalysisResponse, CapabilitiesResponse, ProspectivePrediction, ProspectiveOpportunity, ProspectivePerformance } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_MODELFC_API_URL;
// A deployment must opt into mock mode explicitly. Missing configuration is
// surfaced as an API_CONFIGURATION_ERROR instead of masquerading as a demo.
export const apiMode = process.env.NEXT_PUBLIC_MODELFC_API_MODE ?? "";

async function requestJson<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (isApiErrorBody(body)) {
      throw new ModelFCApiError(body.error.message, body.error.code, body.error.retryable, response.status);
    }
    throw new ModelFCApiError("Model FC API request failed.", "NETWORK_ERROR", response.status >= 500, response.status);
  }
  return body as T;
}

function decodeAnalysisResponse(value: unknown, request: AnalysisRequest): AnalysisResponse {
  const record = (item: unknown): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item));
  const stringOrNull = (item: unknown): item is string | null => item === null || typeof item === "string";
  const finiteNumber = (item: unknown): item is number => typeof item === "number" && Number.isFinite(item);
  const probability = (item: unknown): item is number => finiteNumber(item) && item >= 0 && item <= 1;
  const warning = (item: unknown) => record(item)
    && typeof item.code === "string"
    && typeof item.message === "string";
  const warnings = (item: unknown) => Array.isArray(item) && item.every(warning);
  const configuration = (item: unknown) => record(item)
    && Object.values(item).every((entry) => entry === null || typeof entry === "string" || finiteNumber(entry));
  const sourceHash = (item: unknown) => record(item)
    && typeof item.filename === "string"
    && typeof item.sha256 === "string";
  const marketResult = (item: Record<string, unknown>) => {
    if (item.status === "SUPPORTED") {
      return item.unsupported_reason === null
        && probability(item.model_probability)
        && probability(item.push_probability)
        && probability(item.decisive_model_probability)
        && probability(item.implied_probability)
        && finiteNumber(item.probability_edge)
        && finiteNumber(item.expected_profit)
        && finiteNumber(item.expected_corners);
    }
    if (item.status !== "UNSUPPORTED"
      || typeof item.unsupported_reason !== "string"
      || !item.unsupported_reason) return false;
    if (item.unsupported_reason === "NO_DECISIVE_OUTCOMES") {
      return probability(item.model_probability)
        && probability(item.push_probability)
        && item.decisive_model_probability === null
        && item.implied_probability === null
        && item.probability_edge === null
        && item.expected_profit === null
        && finiteNumber(item.expected_corners);
    }
    return item.model_probability === null
      && item.push_probability === null
      && item.decisive_model_probability === null
      && item.implied_probability === null
      && item.probability_edge === null
      && item.expected_profit === null
      && item.expected_corners === null;
  };
  const market = (item: unknown) => record(item)
    && typeof item.client_market_id === "string"
    && (item.market_type === "TEAM_TOTAL" || item.market_type === "MATCH_TOTAL")
    && (item.team_side === null || item.team_side === "HOME" || item.team_side === "AWAY")
    && (item.side === "OVER" || item.side === "UNDER")
    && finiteNumber(item.line)
    && Number.isSafeInteger(item.american_odds)
    && stringOrNull(item.team)
    && marketResult(item)
    && warnings(item.warnings);
  const requestMarketMatches = (item: Record<string, unknown>, expected: AnalysisRequest["markets"][number]) =>
    item.client_market_id === expected.client_market_id
    && item.market_type === expected.market_type
    && item.team_side === expected.team_side
    && item.side === expected.side
    && item.line === expected.line
    && item.american_odds === expected.american_odds;
  const requestFixtureMatches = (item: Record<string, unknown>, expected: AnalysisRequest["fixture"]) =>
    item.competition === expected.competition
    && item.date === expected.date
    && item.home_team === expected.home_team
    && item.away_team === expected.away_team;
  const marketTeamMatchesFixture = (item: Record<string, unknown>, fixture: unknown) => {
    if (item.market_type !== "TEAM_TOTAL") return true;
    if (!record(fixture)) return false;
    const expectedTeam = item.team_side === "HOME"
      ? fixture.home_team
      : item.team_side === "AWAY" ? fixture.away_team : null;
    if (typeof expectedTeam !== "string") return false;
    if (item.status === "SUPPORTED") return item.team === expectedTeam;
    return item.team === null || item.team === expectedTeam;
  };
  const valid = record(value)
    && typeof value.analysis_id === "string"
    && typeof value.forecast_id === "string"
    && typeof value.created_at === "string"
    && record(value.pick_logging)
    && (value.pick_logging.status === "SUPPORTED" || value.pick_logging.status === "DISABLED")
    && stringOrNull(value.pick_logging.reason)
    && record(value.fixture)
    && typeof value.fixture.competition === "string"
    && typeof value.fixture.date === "string"
    && typeof value.fixture.home_team === "string"
    && typeof value.fixture.away_team === "string"
    && stringOrNull(value.fixture.kickoff_at)
    && (value.pick_logging.status !== "SUPPORTED" || typeof value.fixture.kickoff_at === "string")
    && requestFixtureMatches(value.fixture, request.fixture)
    && record(value.forecast)
    && typeof value.forecast.model === "string"
    && value.forecast.model === request.model
    && typeof value.forecast.model_version === "string"
    && configuration(value.forecast.configuration)
    && finiteNumber(value.forecast.home_expected_corners)
    && finiteNumber(value.forecast.away_expected_corners)
    && finiteNumber(value.forecast.match_expected_corners)
    && typeof value.forecast.latest_history_date === "string"
    && Array.isArray(value.forecast.source_data_hashes)
    && value.forecast.source_data_hashes.every(sourceHash)
    && Array.isArray(value.markets)
    && value.markets.every(market)
    && value.markets.length === request.markets.length
    && value.markets.every((item, index) => record(item)
      && requestMarketMatches(item, request.markets[index])
      && marketTeamMatchesFixture(item, value.fixture))
    && warnings(value.warnings);
  if (!valid) {
    throw new ModelFCApiError(
      "The API returned an analysis response outside the V1 contract.",
      "ANALYSIS_CONTRACT_MISMATCH", false,
    );
  }
  return value as unknown as AnalysisResponse;
}

export function createApiClient(mode: string | undefined, baseUrl?: string) {
  function checkConfiguration() {
    if (!mode || !["live", "mock"].includes(mode) || (mode === "live" && !baseUrl?.trim())) {
      throw new ModelFCApiError(
        "Set API mode to mock or live. Live mode also requires NEXT_PUBLIC_MODELFC_API_URL.",
        "API_CONFIGURATION_ERROR", false,
      );
    }
  }
  return {
    async capabilities(signal?: AbortSignal): Promise<CapabilitiesResponse> {
      checkConfiguration();
      const value = mode === "mock" ? structuredClone(mockCapabilities)
        : await requestJson<unknown>(baseUrl!, "/capabilities", { signal });
      return decodeCapabilities(value);
    },
    async predictions(signal?: AbortSignal): Promise<ProspectivePrediction[]> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodePredictions(mode === "mock" ? structuredClone(mockPredictions)
        : await requestJson<unknown>(baseUrl!, "/predictions", { signal }));
    },
    async opportunities(signal?: AbortSignal): Promise<ProspectiveOpportunity[]> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodeOpportunities(mode === "mock" ? structuredClone(mockOpportunities)
        : await requestJson<unknown>(baseUrl!, "/opportunities", { signal }));
    },
    async performance(signal?: AbortSignal): Promise<ProspectivePerformance> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodePerformance(mode === "mock" ? structuredClone(mockPerformance)
        : await requestJson<unknown>(baseUrl!, "/prospective/performance", { signal }));
    },
    async analyze(payload: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
      checkConfiguration();
      return mode === "mock" ? mockAnalyze(payload, signal)
        : requestJson<unknown>(baseUrl!, "/analyses", { method: "POST", body: JSON.stringify(payload), signal })
          .then((value) => decodeAnalysisResponse(value, payload));
    },
  };
}

export const api = createApiClient(apiMode, API_BASE);
