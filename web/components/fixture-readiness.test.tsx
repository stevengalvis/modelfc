import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { FixtureReadinessDashboard } from "./fixture-readiness";
import { getFixtureReadiness } from "@/lib/api/fixture-readiness";
vi.mock("@/lib/api/fixture-readiness", () => ({getFixtureReadiness: vi.fn()}));
describe("saved fixture status", () => {
  it("explains missing calendars, history and E0 support", async () => {
    vi.mocked(getFixtureReadiness).mockResolvedValue({version: 1, observed_at_utc: "2026-10-12T12:00:00Z", generated_at_utc: null, calendar_retrieved_at_utc: null, calendar_state: "MISSING", reason: "PROJECTION_NOT_CONFIGURED", e0_state: "CALENDAR_ADAPTER_REQUIRED", history_coverage: "NOT_ASSESSED", fixtures: []});
    render(<FixtureReadinessDashboard />);
    expect(await screen.findByText("MISSING")).toBeVisible();
    expect(screen.getByText(/E0 BTTS is unsupported/)).toBeVisible();
    expect(screen.getByText(/Goals, corners and shots coverage has not been assessed/)).toBeVisible();
  });
  it("renders each saved fixture once and toggles finished fixtures", async () => {
    const fixtures = ["TIMED", "FINISHED"].map((status, i) => ({fixture_id: i + 1, home_team: "Home", away_team: "Away", kickoff_utc: "2026-10-13T12:00:00Z", status, markets: [{market: "CORNER" as const, state: "NOT_RECORDED" as const, frozen_at_utc: null}, {market: "BTTS" as const, state: "NOT_RECORDED" as const, frozen_at_utc: null}]}));
    vi.mocked(getFixtureReadiness).mockResolvedValue({version: 1, observed_at_utc: "2026-10-12T12:00:00Z", generated_at_utc: "2026-10-08T12:00:00Z", calendar_retrieved_at_utc: "2026-10-08T12:00:00Z", calendar_state: "STALE", reason: null, e0_state: "CALENDAR_ADAPTER_REQUIRED", history_coverage: "NOT_ASSESSED", fixtures});
    render(<FixtureReadinessDashboard />);
    expect(await screen.findAllByRole("article")).toHaveLength(1);
    fireEvent.click(screen.getByLabelText("Include finished fixtures"));
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });
  it("does not replace a failed read with fixtures", async () => {
    vi.mocked(getFixtureReadiness).mockRejectedValue(new Error("secret path"));
    render(<FixtureReadinessDashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
    expect(screen.queryByText(/secret path/)).toBeNull();
  });
});
