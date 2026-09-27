import { isApiErrorBody, ModelFCApiError } from "./errors";
import { mockAnalyze, mockCapabilities } from "./mock";
import { decodeCapabilities } from "./capabilities";
import type { AnalysisRequest, AnalysisResponse, CapabilitiesResponse } from "./types";

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

function decodeAnalysisResponse(value: unknown): AnalysisResponse {
  const record = (item: unknown): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item));
  const stringOrNull = (item: unknown): item is string | null => item === null || typeof item === "string";
  const finiteNumber = (item: unknown): item is number => typeof item === "number" && Number.isFinite(item);
  const finiteNumberOrNull = (item: unknown): item is number | null => item === null || finiteNumber(item);
  const probabilityOrNull = (item: unknown): item is number | null => item === null
    || (finiteNumber(item) && item >= 0 && item <= 1);
  const warning = (item: unknown) => record(item)
    && typeof item.code === "string"
    && typeof item.message === "string";
  const warnings = (item: unknown) => Array.isArray(item) && item.every(warning);
  const configuration = (item: unknown) => record(item)
    && Object.values(item).every((entry) => entry === null || typeof entry === "string" || finiteNumber(entry));
  const sourceHash = (item: unknown) => record(item)
    && typeof item.filename === "string"
    && typeof item.sha256 === "string";
  const market = (item: unknown) => record(item)
    && typeof item.client_market_id === "string"
    && (item.market_type === "TEAM_TOTAL" || item.market_type === "MATCH_TOTAL")
    && (item.team_side === null || item.team_side === "HOME" || item.team_side === "AWAY")
    && (item.side === "OVER" || item.side === "UNDER")
    && finiteNumber(item.line)
    && Number.isSafeInteger(item.american_odds)
    && stringOrNull(item.team)
    && (item.status === "SUPPORTED" || item.status === "UNSUPPORTED")
    && stringOrNull(item.unsupported_reason)
    && probabilityOrNull(item.model_probability)
    && probabilityOrNull(item.push_probability)
    && probabilityOrNull(item.decisive_model_probability)
    && probabilityOrNull(item.implied_probability)
    && finiteNumberOrNull(item.probability_edge)
    && finiteNumberOrNull(item.expected_profit)
    && finiteNumberOrNull(item.expected_corners)
    && warnings(item.warnings);
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
    && record(value.forecast)
    && typeof value.forecast.model === "string"
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
    async analyze(payload: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
      checkConfiguration();
      return mode === "mock" ? mockAnalyze(payload, signal)
        : requestJson<unknown>(baseUrl!, "/analyses", { method: "POST", body: JSON.stringify(payload), signal }).then(decodeAnalysisResponse);
    },
  };
}

export const api = createApiClient(apiMode, API_BASE);
