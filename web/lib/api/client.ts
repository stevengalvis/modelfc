import { isApiErrorBody, ModelFCApiError } from "./errors";
import { mockAnalyze, mockCapabilities } from "./mock";
import { decodeCapabilities } from "./capabilities";
import type { AnalysisRequest, AnalysisResponse, CapabilitiesResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_MODELFC_API_URL;
export const apiMode = process.env.NEXT_PUBLIC_MODELFC_API_MODE ?? "mock";

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

export function createApiClient(mode: string, baseUrl?: string) {
  function checkConfiguration() {
    if (!["live", "mock"].includes(mode) || (mode === "live" && !baseUrl?.trim())) {
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
        : requestJson<AnalysisResponse>(baseUrl!, "/analyses", { method: "POST", body: JSON.stringify(payload), signal });
    },
  };
}

export const api = createApiClient(apiMode, API_BASE);
