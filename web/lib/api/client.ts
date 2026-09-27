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
  const valid = record(value)
    && typeof value.analysis_id === "string"
    && typeof value.forecast_id === "string"
    && typeof value.created_at === "string"
    && record(value.pick_logging)
    && (value.pick_logging.status === "SUPPORTED" || value.pick_logging.status === "DISABLED")
    && (value.pick_logging.reason === null || typeof value.pick_logging.reason === "string")
    && record(value.fixture)
    && typeof value.fixture.competition === "string"
    && typeof value.fixture.date === "string"
    && typeof value.fixture.home_team === "string"
    && typeof value.fixture.away_team === "string"
    && record(value.forecast)
    && typeof value.forecast.model === "string"
    && typeof value.forecast.model_version === "string"
    && Array.isArray(value.markets)
    && Array.isArray(value.warnings);
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
