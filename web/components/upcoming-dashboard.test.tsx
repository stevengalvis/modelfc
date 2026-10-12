import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api/client";
import { mockPredictions } from "../lib/api/mock";
import { emptyBttsPerformance } from "../lib/api/btts-performance";
import { upcomingFixtures } from "../lib/upcoming";
import { UpcomingDashboard } from "./upcoming-dashboard";

const now = Date.parse("2026-10-12T12:00:00Z");
const corner = { ...mockPredictions[0], competition: "E1", home_team: "Home", away_team: "Away",
  kickoff_utc: "2026-10-13T12:00:00Z", created_at_utc: "2026-10-12T11:00:00Z", settlement_status: "UPCOMING" as const };
const btts = { ...corner, prediction_id: "btts", prediction_type: "BTTS" as const,
  btts_yes_probability: 0.57, btts_no_probability: 0.43, research_status: "RESEARCH_ONLY" as const };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function sources() {
  vi.spyOn(Date,"now").mockReturnValue(now);
  vi.spyOn(api,"predictions").mockResolvedValue([corner,btts]);
  vi.spyOn(api,"bttsPerformance").mockResolvedValue(emptyBttsPerformance);
  vi.spyOn(api,"recommendations").mockResolvedValue([]);
}
describe("next Championship matches", () => {
  it("groups independent markets and repeated cohorts into one exact fixture", () => {
    const later = {...corner,prediction_id:"later",created_at_utc:"2026-10-12T11:30:00Z",expected_home_corners:99};
    const cards=upcomingFixtures([later,btts,corner],[],now);
    expect(cards).toHaveLength(1);
    expect(cards[0].corner?.values[0]).toBe(corner.expected_home_corners);
    expect(cards[0].btts?.values).toEqual([0.57,0.43]);
    expect(upcomingFixtures([{...corner,competition:"E0"},{...corner,kickoff_utc:"2026-10-12T11:00:00Z"}],[],now)).toEqual([]);
    expect(upcomingFixtures([corner,{...corner,kickoff_utc:"2026-10-13T13:00:00Z"}],[],now)).toHaveLength(2);
  });
  it("shows both market values, sample count, freeze age and history links", async () => {
    sources(); render(<UpcomingDashboard/>);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByText(/1 unique upcoming fixtures/)).toBeInTheDocument();
    expect(screen.getByText("57.0%")).toBeInTheDocument();
    expect(screen.getByText("43.0%")).toBeInTheDocument();
    expect(screen.getByText("Home corners")).toBeInTheDocument();
    expect(screen.getAllByText(/Frozen 1 hr ago/)).toHaveLength(2);
    expect(screen.getByRole("link",{name:"BTTS results"})).toHaveAttribute("href","/research/btts/performance");
  });
  it("does not hide a valid market when the other read fails; retry recovers", async () => {
    sources(); vi.mocked(api.predictions).mockResolvedValue([corner]);
    vi.mocked(api.bttsPerformance).mockRejectedValueOnce(new Error("private failure"));
    render(<UpcomingDashboard/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("BTTS");
    expect(screen.getByText("Home corners")).toBeInTheDocument();
    expect(screen.getByText("Blocked · read unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"Refresh"}));
    await waitFor(()=>expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText("Waiting · no recorded forecast")).toBeInTheDocument();
  });
  it("does not invent an upcoming schedule when only history or no data exists", async () => {
    sources(); vi.mocked(api.predictions).mockResolvedValue([{...corner,kickoff_utc:"2026-10-11T12:00:00Z"}]);
    render(<UpcomingDashboard/>);
    expect(await screen.findByText("No upcoming forecasts recorded")).toBeInTheDocument();
    expect(screen.getByText(/not the full fixture calendar/)).toBeInTheDocument();
  });
  it("distinguishes total source outage from empty successful reads", async () => {
    sources(); vi.mocked(api.predictions).mockRejectedValue(new Error("down"));
    vi.mocked(api.bttsPerformance).mockRejectedValue(new Error("down"));
    render(<UpcomingDashboard/>);
    expect(await screen.findByText("Upcoming coverage unavailable or incomplete")).toBeInTheDocument();
    expect(screen.queryByText("No upcoming forecasts recorded")).not.toBeInTheDocument();
  });
});
