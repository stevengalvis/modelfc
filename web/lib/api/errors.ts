import type { ApiErrorBody } from "./types";

export class ModelFCApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly retryable = false,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ModelFCApiError";
  }
}

export function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (!value || typeof value !== "object" || !("error" in value)) return false;
  const error = (value as ApiErrorBody).error;
  return Boolean(error && typeof error.code === "string" && typeof error.message === "string");
}

export function describeApiError(cause: unknown): { title: string; message: string } {
  if (!(cause instanceof ModelFCApiError)) {
    return { title: "API connection failed", message: "Could not reach the API. Check the connection and try again. No mock results were substituted." };
  }
  const title = cause.code === "DATA_SOURCE_UNAVAILABLE" ? "Data unavailable"
    : cause.code === "INSUFFICIENT_HISTORY" ? "Insufficient history"
    : cause.code === "UNKNOWN_TEAM" ? "Team correction required"
    : cause.code === "UNSUPPORTED_COMPETITION" ? "Competition unavailable"
    : cause.code === "CAPABILITY_CONTRACT_MISMATCH" ? "Backend contract needs updating"
    : cause.code === "DEMO_FIXTURE_ONLY" ? "Outside the fixed demo"
    : cause.status === 422 ? "Input validation failed" : "API request failed";
  return { title, message: `${cause.code}: ${cause.message}` };
}
