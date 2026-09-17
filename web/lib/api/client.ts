import { isApiErrorBody, ModelFCApiError } from "./errors";
import { mockAnalyze, mockCapabilities } from "./mock";
import type { AnalysisRequest, AnalysisResponse, CapabilitiesResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_MODELFC_API_URL ?? "http://localhost:8000/api/v1";
const USE_MOCKS = process.env.NEXT_PUBLIC_MODELFC_API_MODE !== "live";
export const apiMode: "mock" | "live" = USE_MOCKS ? "mock" : "live";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
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

export const api = {
  capabilities(signal?: AbortSignal): Promise<CapabilitiesResponse> {
    return USE_MOCKS ? Promise.resolve(mockCapabilities) : requestJson("/capabilities", { signal });
  },
  analyze(payload: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
    return USE_MOCKS
      ? mockAnalyze(payload, signal)
      : requestJson("/analyses", { method: "POST", body: JSON.stringify(payload), signal });
  },
};
