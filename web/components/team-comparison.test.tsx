import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import teams from "../../tests/fixtures/team_intelligence/teams.json";
import profiles from "../../tests/fixtures/team_intelligence/profiles.json";
import {
  decodeTeamProfile,
  decodeTeams,
  type TeamProfile,
} from "@/lib/api/team-intelligence";
import { TeamComparison } from "./team-comparison";

const reads = vi.hoisted(() => ({ teams: vi.fn(), teamProfile: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: reads }));
afterEach(cleanup);
const profile = (id: string) =>
  decodeTeamProfile(structuredClone(profiles[id as keyof typeof profiles]), id);
beforeEach(() => {
  vi.clearAllMocks();
  reads.teams.mockResolvedValue(decodeTeams(teams));
  reads.teamProfile.mockImplementation(async (id) => profile(id));
});
async function choose(left = "cardiff", right = "birmingham") {
  await screen.findByLabelText("First team");
  fireEvent.change(screen.getByLabelText("First team"), {
    target: { value: left },
  });
  fireEvent.change(screen.getByLabelText("Second team"), {
    target: { value: right },
  });
}
it("requires two distinct current teams and preselects the profile's team", async () => {
  render(<TeamComparison initialTeam="cardiff" />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
  expect(await screen.findByLabelText("First team")).toHaveValue("cardiff");
  expect(reads.teamProfile).not.toHaveBeenCalled();
  await choose("cardiff", "cardiff");
  expect(screen.getByRole("status")).toHaveTextContent("different teams");
  expect(reads.teamProfile).not.toHaveBeenCalled();
});
it("shows backend season, venue and recent values, dates, denominators and profile links", async () => {
  render(<TeamComparison />);
  await choose();
  await screen.findByRole("region", { name: "Compared teams" });
  expect(
    screen.getByRole("link", { name: "Cardiff City profile →" }),
  ).toHaveAttribute("href", "/teams/cardiff");
  for (const title of [
    "Season",
    "Home",
    "Away",
    "Last 5",
    "Previous 5",
    "Last 10",
  ])
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  const season = screen
    .getByRole("heading", { name: "Season" })
    .closest("section")!;
  expect(
    within(season).getByText("12 of 12 matches covered"),
  ).toBeInTheDocument();
  expect(
    within(season).getByText(
      profile("cardiff").summary.primary.won!.toFixed(2),
    ),
  ).toBeInTheDocument();
  expect(screen.getAllByText(/2026-08-01 to/).length).toBeGreaterThan(0);
  expect(
    screen.getAllByText("Unavailable: missing corner data").length,
  ).toBeGreaterThan(0);
  expect(screen.getByText(/Latest corners: 2026-08-17/)).toBeInTheDocument();
  expect(reads.teamProfile).toHaveBeenCalledTimes(2);
});
it("shows short samples without pretending they are full recent windows", async () => {
  render(<TeamComparison />);
  await choose("portsmouth", "millwall");
  await screen.findByRole("region", { name: "Compared teams" });
  expect(
    screen.getAllByText("Not enough completed matches").length,
  ).toBeGreaterThan(0);
  expect(screen.getAllByText(/2 of 2 matches covered/).length).toBeGreaterThan(
    0,
  );
});
it("keeps an empty directory distinct from invalid team selection", async () => {
  reads.teams.mockResolvedValueOnce({
    ...decodeTeams(teams),
    teams: [],
    insights: [],
  });
  const view = render(<TeamComparison />);
  await screen.findByText(/No completed current-season/);
  expect(reads.teamProfile).not.toHaveBeenCalled();
  view.unmount();
  render(<TeamComparison initialTeam="not-a-team" />);
  await screen.findByText(/Selected team is not in the current directory/);
  expect(reads.teamProfile).not.toHaveBeenCalled();
  await choose();
  await screen.findByRole("region", { name: "Compared teams" });
});
it("retries directory failures without replacing them with mock data", async () => {
  reads.teams.mockRejectedValueOnce(new Error("History unavailable"));
  render(<TeamComparison />);
  await screen.findByRole("alert");
  expect(screen.queryByLabelText("First team")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByLabelText("First team");
  expect(reads.teams).toHaveBeenCalledTimes(2);
});
it("renders neither profile when a selected profile fails and retries both", async () => {
  reads.teamProfile.mockRejectedValueOnce(new Error("Team was not found"));
  render(<TeamComparison />);
  await choose();
  await screen.findByRole("alert");
  expect(
    screen.queryByRole("region", { name: "Compared teams" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("region", { name: "Compared teams" });
  expect(reads.teamProfile).toHaveBeenCalledTimes(4);
});
it("rejects a revision mismatch even when the displayed cutoff is unchanged", async () => {
  const changed = profile("birmingham");
  changed.metadata.source_revision = "f".repeat(64);
  reads.teamProfile.mockImplementation(async (id) =>
    id === "birmingham" ? changed : profile(id),
  );
  render(<TeamComparison />);
  await choose();
  expect(await screen.findByRole("alert")).toHaveTextContent("source changed");
  expect(
    screen.queryByRole("region", { name: "Compared teams" }),
  ).not.toBeInTheDocument();
  reads.teamProfile.mockImplementation(async (id) => profile(id));
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("region", { name: "Compared teams" });
});
it("ignores delayed old selections even when the transport ignores abort", async () => {
  let resolve!: (value: TeamProfile) => void;
  reads.teamProfile.mockImplementation((id) =>
    id === "birmingham"
      ? new Promise((r) => {
          resolve = r;
        })
      : Promise.resolve(profile(id)),
  );
  render(<TeamComparison />);
  await choose();
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
  fireEvent.change(screen.getByLabelText("Second team"), {
    target: { value: "portsmouth" },
  });
  await screen.findByRole("link", { name: "Portsmouth profile →" });
  await act(async () => resolve(profile("birmingham")));
  expect(
    screen.queryByRole("link", { name: "Birmingham City profile →" }),
  ).not.toBeInTheDocument();
  expect(reads.teamProfile.mock.calls[0][1].aborted).toBe(true);
});
it("clears completed results when a selection becomes empty or the same team", async () => {
  render(<TeamComparison />);
  await choose();
  await screen.findByRole("region", { name: "Compared teams" });
  fireEvent.change(screen.getByLabelText("Second team"), {
    target: { value: "" },
  });
  expect(
    screen.queryByRole("region", { name: "Compared teams" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Choose two teams");
  await choose();
  await screen.findByRole("region", { name: "Compared teams" });
  fireEvent.change(screen.getByLabelText("Second team"), { target: { value: "cardiff" } });
  expect(screen.queryByRole("region", { name: "Compared teams" })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("different teams");
});
