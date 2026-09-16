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
