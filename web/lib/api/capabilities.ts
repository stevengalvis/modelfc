import { ModelFCApiError } from "./errors";
import type { CapabilitiesResponse, CompetitionCapability, MarketType, TeamSide, Warning } from "./types";

export const marketLabels: Record<MarketType, string> = {
  TEAM_TOTAL: "Team total",
  MATCH_TOTAL: "Match total",
};

export function findCompetition(capabilities: CapabilitiesResponse | null, code: string) {
  return capabilities?.competitions.find((item) => item.code === code.trim().toUpperCase()) ?? null;
}

export function eligibleTeams(competition: CompetitionCapability | null, side: TeamSide): string[] {
  // No fallback to the legacy all-venue list: older PR #49 responses listed ineligible teams.
  return competition?.analysis ? competition.teams_by_side?.[side] ?? [] : [];
}

export function unavailableMarketReason(
  type: MarketType, competition: CompetitionCapability | null, capabilities: CapabilitiesResponse | null,
): string | null {
  if (!capabilities) return "Market availability has not loaded.";
  if (!competition?.analysis) return "The selected competition is unavailable for analysis.";
  if (competition.markets.includes(type)) return null;
  const reason = capabilities.market_capabilities.find((item) => item.market_type === type)?.reason;
  const warning = type === "MATCH_TOTAL"
    ? competition.warnings.find((item) => item.code === "MATCH_TOTAL_NOT_VALIDATED")?.message
    : undefined;
  return [reason, warning ?? `${marketLabels[type]} is not enabled for ${competition.name}.`].filter(Boolean).join(": ");
}

export function competitionIssue(competition: CompetitionCapability): { title: string; message: string } | null {
  if (competition.analysis) return null;
  const codes = competition.warnings.map((item) => item.code);
  const title = codes.includes("DATA_SOURCE_UNAVAILABLE") ? "Data unavailable"
    : codes.includes("INSUFFICIENT_HISTORY") ? "Insufficient history"
    : "Competition unavailable";
  return { title, message: competition.warnings.map((item) => `${item.code}: ${item.message}`).join(" ")
    || `${competition.name} is not ready for analysis.` };
}

// Validate the readiness-critical wire fields before allowing any live request.
// In particular, reject the older contract without teams_by_side.
export function decodeCapabilities(value: unknown): CapabilitiesResponse {
  const record = (item: unknown): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item));
  const strings = (item: unknown): item is string[] => Array.isArray(item) && item.every((v) => typeof v === "string");
  const markets = (item: unknown): boolean => strings(item) && item.every((v) => v === "TEAM_TOTAL" || v === "MATCH_TOTAL");
  const nullableString = (item: unknown) => item === null || typeof item === "string";
  const warnings = (item: unknown): item is Warning[] => Array.isArray(item) && item.every((v) => record(v) && typeof v.code === "string" && typeof v.message === "string");
  const valid = record(value) && value.api_version === "v1" && value.stake === 1
    && strings(value.models) && value.models.length > 0 && markets(value.markets)
    && Array.isArray(value.market_capabilities) && value.market_capabilities.every((v) =>
      record(v) && markets([v.market_type]) && ["SUPPORTED", "UNAVAILABLE"].includes(String(v.status)) && nullableString(v.reason))
    && Array.isArray(value.competitions) && value.competitions.every((v) =>
      record(v) && typeof v.code === "string" && typeof v.name === "string" && typeof v.provider === "string"
      && typeof v.analysis === "boolean" && markets(v.markets) && strings(v.teams)
      && record(v.teams_by_side) && strings(v.teams_by_side.HOME) && strings(v.teams_by_side.AWAY)
      && typeof v.automatic_refresh === "boolean" && v.refresh_job_status === "UNVERIFIED"
      && (v.last_refresh_status === null || ["SUCCEEDED", "FAILED"].includes(String(v.last_refresh_status)))
      && ["SUPPORTED", "MANUAL_ONLY"].includes(String(v.automatic_settlement))
      && nullableString(v.trusted_kickoff_source) && nullableString(v.latest_result_date)
      && nullableString(v.last_refresh_at) && typeof v.stale === "boolean" && warnings(v.warnings));
  if (!valid) {
    throw new ModelFCApiError(
      "The API must provide competition markets, venue-eligible teams_by_side, and refresh status from the corrected capability contract.",
      "CAPABILITY_CONTRACT_MISMATCH", false,
    );
  }
  return value as unknown as CapabilitiesResponse;
}
