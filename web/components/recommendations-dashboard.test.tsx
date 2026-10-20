import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { decodeStrategyPage, decodeTrackRecord, type StrategyPage } from "@/lib/api/track-record";
import fixture from "../../tests/fixtures/strategy_picks.json";
import cornerFixture from "../../tests/fixtures/track_record.json";
import { RecommendationsDashboard } from "./recommendations-dashboard";

const base = decodeStrategyPage(fixture);
function page(offset = 0, total = 1): StrategyPage {
  return { ...base, offset, total, next_offset: offset + 5 < total ? offset + 5 : null,
    publications: Array.from({length: Math.min(5, total - offset)}, (_, i) => ({ ...base.publications[0],
      publication_id: `pick-${offset + i}`, offer: { ...base.publications[0].offer, home_team: `Home ${offset + i}` } })) };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

it("defaults to Higher Probability, places filters below tabs, and ignores old shortlist links", async () => {
  window.history.replaceState({}, "", "/recommendations?view=shortlist");
  const read = vi.spyOn(api, "strategyPicks").mockResolvedValue(page());
  const shortlist = vi.spyOn(api, "shortlist"); const alternatives = vi.spyOn(api, "alternatives");
  render(<RecommendationsDashboard />); await screen.findByRole("article");
  expect(screen.getAllByRole("tab").map(t => t.textContent)).toEqual(["Higher Probability", "Best Value"]);
  expect(screen.getAllByRole("tab")[0]).toHaveAttribute("aria-selected", "true");
  expect(read.mock.calls[0][0]).toMatchObject({ strategy: "higher_probability", scope: "current", offset: 0 });
  expect(screen.getByRole("tablist").nextElementSibling).toContainElement(screen.getByLabelText("Market"));
  expect(screen.getByRole("tablist").nextElementSibling).toContainElement(screen.getByLabelText("League"));
  expect(screen.getAllByRole("combobox")).toHaveLength(2);
  expect(screen.queryByText(/Saved-price analysis|alternative-price analysis|Ranking/)).not.toBeInTheDocument();
  expect(shortlist).not.toHaveBeenCalled(); expect(alternatives).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", {name: /Show more|Previous picks|Next picks/})).not.toBeInTheDocument();
});

it("supports arrow/home/end tab navigation, linked panels and independent closed information", async () => {
  const read = vi.spyOn(api, "strategyPicks").mockResolvedValue(page(0, 2));
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  const help = screen.getByRole("button", {name: /How these picks/});
  expect(help).toHaveAttribute("aria-expanded", "false");
  expect(document.getElementById(help.getAttribute("aria-controls")!)).not.toBeVisible();
  fireEvent.click(help); expect(help).toHaveAttribute("aria-expanded", "true");
  const info = screen.getAllByRole("button", {name: /Pick information/});
  fireEvent.click(info[0]); expect(info[0]).toHaveAttribute("aria-expanded", "true");
  expect(info[1]).toHaveAttribute("aria-expanded", "false");
  expect(document.getElementById(info[0].getAttribute("aria-controls")!)).toBeVisible();
  fireEvent.click(info[0]); expect(info[0]).toHaveAttribute("aria-expanded", "false");
  const tabs = screen.getAllByRole("tab");
  for (const [key, index] of [["ArrowRight", 1], ["Home", 0], ["End", 1], ["ArrowLeft", 0]] as const) {
    fireEvent.keyDown(document.activeElement?.getAttribute("role") === "tab" ? document.activeElement : tabs[0], {key});
    await screen.findAllByRole("article");
    expect(tabs[index]).toHaveFocus(); expect(tabs[index]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[index].id);
    expect(screen.getByRole("button", {name: /How these picks/})).toHaveAttribute("aria-expanded", "false");
  }
  expect(read.mock.lastCall?.[0].strategy).toBe("higher_probability");
});

it("reaches every pick in bounded batches, retains order, announces counts and focuses new picks", async () => {
  const read = vi.spyOn(api, "strategyPicks").mockImplementation(async query => page(query.offset, 12));
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  expect(screen.getAllByRole("article")).toHaveLength(5); expect(read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"}));
  await screen.findByText(/Showing 10/); expect(screen.getAllByRole("article")[5]).toHaveFocus();
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"}));
  await screen.findByText(/All matching picks shown/);
  expect(screen.getAllByRole("article").map(a => within(a).getByRole("heading").textContent)).toEqual(Array.from({length: 12}, (_, i) => `Home ${i} vs ${base.publications[0].offer.away_team}`));
  expect(read.mock.calls.map(c => c[0].offset)).toEqual([0, 5, 0, 10, 0, 5]);
  expect(screen.queryByRole("button", {name: "Show more picks"})).not.toBeInTheDocument();
});

it("retains loaded cards on failure and retries the same batch without duplicates", async () => {
  let resolve!: (p: StrategyPage) => void;
  const read = vi.spyOn(api, "strategyPicks").mockResolvedValueOnce(page(0, 6)).mockRejectedValueOnce(new Error()).mockImplementationOnce(() => new Promise(r => {resolve = r;})).mockResolvedValueOnce(page(0, 6));
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"})); await screen.findByRole("alert");
  expect(screen.getAllByRole("article")).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", {name: "Retry strategy picks"}));
  expect(screen.getByRole("button", {name: "Retry strategy picks"})).toBeDisabled();
  await act(async () => resolve(page(5, 6)));
  expect(screen.getAllByRole("article")).toHaveLength(6); expect(read.mock.calls.map(c => c[0].offset)).toEqual([0, 5, 5, 0]);
});

it("resets accumulated picks and disclosures on each filter/strategy and ignores aborted responses", async () => {
  const read = vi.spyOn(api, "strategyPicks").mockImplementation(async q => page(q.offset, 6));
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"})); await screen.findByText(/Showing 6/);
  fireEvent.click(screen.getAllByRole("button", {name: /Pick information/})[0]);
  for (const [name, value] of [["Market", "BTTS"], ["Market", "TEAM_TOTAL"], ["League", "E1"]]) {
    fireEvent.change(screen.getByLabelText(name), {target: {value}}); await screen.findByText(/Showing 5/);
    expect(read.mock.lastCall?.[0].offset).toBe(0);
    expect(screen.getAllByRole("button", {name: /Pick information/})[0]).toHaveAttribute("aria-expanded", "false");
  }
  let resolve!: (p: StrategyPage) => void;
  read.mockImplementationOnce(() => new Promise(r => {resolve = r;})).mockResolvedValueOnce(page(0, 6));
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"}));
  const signal = read.mock.lastCall?.[1];
  fireEvent.click(screen.getByRole("tab", {name: "Best Value"})); await screen.findByText(/Showing 5/);
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(page(5, 6))); expect(screen.getAllByRole("article")).toHaveLength(5);
});

it("distinguishes initial errors from empty results and hides useless navigation", async () => {
  vi.spyOn(api, "strategyPicks").mockRejectedValueOnce(new Error()).mockResolvedValue(page(0, 0));
  render(<RecommendationsDashboard />); await screen.findByRole("alert");
  expect(screen.queryByText(/No published picks/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "Retry strategy picks"})); await screen.findByText(/No published picks/);
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: /Show more|Next picks|Previous picks/})).not.toBeInTheDocument();
});

it("refuses overlapping or changed batches and offers a fresh bounded read", async () => {
  vi.spyOn(api, "strategyPicks").mockResolvedValueOnce(page(0, 6)).mockResolvedValueOnce({...page(5, 6), publications: page().publications}).mockResolvedValue(page(0, 6));
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"})); await screen.findByRole("alert");
  expect(screen.getAllByRole("article")).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", {name: "Reload picks"})); await screen.findByText(/Showing 5/);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps essential prices and material warnings visible with audit details closed", async () => {
  const data = page(0, 3);
  data.publications[0].offer.kickoff_utc = "2099-01-01T00:00:00Z";
  data.publications[1].result = "SIMULATION_VOID";
  data.publications[2].offer.kickoff_utc = "2000-01-01T00:00:00Z";
  vi.spyOn(api, "strategyPicks").mockResolvedValue(data);
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  expect(screen.getByText(/Stale quote/)).toBeVisible(); expect(screen.getByText(/Voided pick · excluded/)).toBeVisible(); expect(screen.getByText(/Started fixture/)).toBeVisible();
  expect(screen.getAllByText("Recorded price · not live")).toHaveLength(3);
  expect(screen.getAllByText(/Frozen publication price/)[0]).not.toBeVisible();
  expect(screen.getAllByText("+120")[0]).toBeVisible(); expect(screen.getAllByText(/67.0%/)[0]).toBeVisible();
});

it("shows an exact corner selection and preserves its evidence link inside closed details", async () => {
  const data = page();
  data.publications[0].offer = decodeTrackRecord(cornerFixture).publications[0].offer;
  vi.spyOn(api, "strategyPicks").mockResolvedValue(data);
  render(<RecommendationsDashboard />);
  expect(await screen.findByText("West Brom corners under 4.5")).toBeVisible();
  expect(screen.getByText("-200")).toBeVisible();
  expect(screen.getByText("fanduel")).toBeVisible();
  expect(screen.queryByRole("link", {name: "Recorded opportunity evidence"})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: /Pick information/}));
  expect(screen.getByRole("link", {name: "Recorded opportunity evidence"})).toHaveAttribute("href", `/opportunities/${cornerFixture.publications[0].offer.opportunity_id}`);
});

it("rejects equal-total non-overlapping pages when kickoff and a new pick change the prefix", async () => {
  const first = page(0, 6), next = page(5, 6), changed = page(0, 6);
  changed.publications[0].publication_id = "newly-published";
  changed.publications[0].offer.home_team = "New highest-ranked pick";
  const read = vi.spyOn(api, "strategyPicks").mockResolvedValueOnce(first)
    .mockResolvedValueOnce(next).mockResolvedValueOnce(changed).mockResolvedValueOnce(changed);
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("published list changed");
  expect(screen.getAllByRole("article")).toHaveLength(5);
  expect(screen.queryByText(/All matching picks shown/)).not.toBeInTheDocument();
  expect(read.mock.calls.map(c => c[0].offset)).toEqual([0, 5, 0]);
  fireEvent.click(screen.getByRole("button", {name: "Reload picks"}));
  await screen.findByRole("heading", {name: /New highest-ranked pick/});
  expect(screen.getAllByRole("article")).toHaveLength(5);
});

it("does not send a future cutoff when the browser clock is ahead of the server", async () => {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2099-01-01T00:00:00Z"));
  const read = vi.spyOn(api, "strategyPicks").mockImplementation(async query => {
    if (query.asOf) throw new Error("INVALID_AS_OF");
    return page(query.offset, 6);
  });
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"}));
  await screen.findByText(/All matching picks shown/);
  expect(screen.getAllByRole("article")).toHaveLength(6);
  expect(read.mock.calls.every(c => c[0].asOf === undefined)).toBe(true);
});

it("retains the prefix and retries safely when prefix revalidation fails", async () => {
  const read = vi.spyOn(api, "strategyPicks").mockResolvedValueOnce(page(0, 6))
    .mockResolvedValueOnce(page(5, 6)).mockRejectedValueOnce(new Error("recheck failed"))
    .mockResolvedValueOnce(page(5, 6)).mockResolvedValueOnce(page(0, 6));
  render(<RecommendationsDashboard />); await screen.findAllByRole("article");
  fireEvent.click(screen.getByRole("button", {name: "Show more picks"}));
  await screen.findByRole("alert"); expect(screen.getAllByRole("article")).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", {name: "Retry strategy picks"}));
  await screen.findByText(/All matching picks shown/);
  expect(screen.getAllByRole("article")).toHaveLength(6);
  expect(read.mock.calls.map(c => c[0].offset)).toEqual([0, 5, 0, 5, 0]);
});
