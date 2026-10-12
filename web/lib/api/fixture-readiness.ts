export type SavedFixture = { fixture_id: number; home_team: string; away_team: string; kickoff_utc: string; status: string; markets: {market: "CORNER" | "BTTS"; state: "RECORDED" | "NOT_RECORDED"; frozen_at_utc: string | null}[] };
export type FixtureReadiness = { version: 1; observed_at_utc: string; generated_at_utc: string | null; calendar_retrieved_at_utc: string | null; calendar_state: "FRESH" | "STALE" | "MISSING" | "UNAVAILABLE"; reason: string | null; e0_state: "CALENDAR_ADAPTER_REQUIRED"; history_coverage: "NOT_ASSESSED"; fixtures: SavedFixture[] };
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const time = (v: unknown): v is string => typeof v === "string" && /(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v));
export function decodeFixtureReadiness(v: unknown): FixtureReadiness {
  if (!record(v) || v.version !== 1 || !time(v.observed_at_utc)
    || !["FRESH", "STALE", "MISSING", "UNAVAILABLE"].includes(String(v.calendar_state))
    || v.e0_state !== "CALENDAR_ADAPTER_REQUIRED" || v.history_coverage !== "NOT_ASSESSED"
    || !(v.reason === null || ["PROJECTION_NOT_CONFIGURED", "PROJECTION_UNAVAILABLE"].includes(String(v.reason)))
    || !Array.isArray(v.fixtures) || v.fixtures.length > 552) throw new Error("Invalid fixture status response");
  const available = v.calendar_state === "FRESH" || v.calendar_state === "STALE";
  if (available ? (!time(v.generated_at_utc) || !time(v.calendar_retrieved_at_utc) || v.fixtures.length !== 552)
    : (v.generated_at_utc !== null || v.calendar_retrieved_at_utc !== null || v.fixtures.length !== 0)) throw new Error("Invalid calendar state");
  const ids = new Set<number>();
  for (const f of v.fixtures) {
    if (!record(f) || typeof f.fixture_id !== "number" || !Number.isSafeInteger(f.fixture_id) || f.fixture_id <= 0 || ids.has(f.fixture_id)
      || typeof f.home_team !== "string" || typeof f.away_team !== "string" || !time(f.kickoff_utc)
      || !["SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "SUSPENDED", "POSTPONED", "CANCELLED", "FINISHED"].includes(String(f.status))
      || !Array.isArray(f.markets) || f.markets.length !== 2) throw new Error("Invalid fixture");
    ids.add(f.fixture_id);
    f.markets.forEach((m, i) => {
      if (!record(m) || m.market !== ["CORNER", "BTTS"][i] || !["RECORDED", "NOT_RECORDED"].includes(String(m.state))
        || (m.state === "RECORDED" ? !time(m.frozen_at_utc) : m.frozen_at_utc !== null)) throw new Error("Invalid market status");
    });
  }
  return v as unknown as FixtureReadiness;
}
export async function getFixtureReadiness(): Promise<FixtureReadiness> {
  const base = process.env.NEXT_PUBLIC_MODELFC_API_URL;
  if (process.env.NEXT_PUBLIC_MODELFC_API_MODE !== "live" || !base) throw new Error("Saved fixture API is not configured.");
  const response = await fetch(`${base.replace(/\/$/, "")}/api/v1/fixture-readiness`, {cache: "no-store"});
  if (!response.ok) throw new Error("Saved fixture status is unavailable.");
  return decodeFixtureReadiness(await response.json());
}
