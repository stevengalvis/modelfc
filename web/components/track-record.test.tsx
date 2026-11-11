import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { decodeTrackRecord, emptyTrackRecord } from "@/lib/api/track-record";
import fixture from "../../tests/fixtures/track_record.json";
import { PublishedTrackRecord } from "./track-record";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("shows original publication times, selections and pending results", async () => {
  vi.spyOn(api, "trackRecord").mockResolvedValue(decodeTrackRecord(fixture));
  render(<PublishedTrackRecord />);
  await screen.findByRole("table");
  expect(screen.getByText("Results of our published recommendations")).toBeInTheDocument();
  expect(screen.getAllByText("PENDING").length).toBeGreaterThan(0);
  expect(screen.getByText(/Sportsbook void settlement is not available/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Published view"), { target: { value: "higher_probability" } });
  expect(api.trackRecord).toHaveBeenCalledTimes(1);
});
it("does not present missing history as a winning or losing record", async () => {
  vi.spyOn(api, "trackRecord").mockResolvedValue(emptyTrackRecord);
  render(<PublishedTrackRecord />);
  expect(await screen.findByText(/No published recommendations yet/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
it("shows unavailable separately from empty and permits retry", async () => {
  vi.spyOn(api, "trackRecord").mockRejectedValueOnce(new Error("unavailable")).mockResolvedValue(emptyTrackRecord);
  render(<PublishedTrackRecord />);
  await screen.findByRole("alert");
  expect(screen.queryByText(/No published recommendations yet/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Retry published record"));
  expect(await screen.findByText(/No published recommendations yet/)).toBeInTheDocument();
});

it("labels BTTS Yes separately from team corners", async () => {
  const btts = (await import("../../tests/fixtures/btts_track_record.json")).default;
  vi.spyOn(api, "trackRecord").mockResolvedValue(decodeTrackRecord(btts));
  render(<PublishedTrackRecord />);
  expect(await screen.findByText("Both teams to score: Yes")).toBeInTheDocument();
  expect(screen.getByText(/E1 · Both teams to score · 1 published picks/)).toBeInTheDocument();
});

it("passes league, market and normalized cutoff together and explains evidence limits", async () => {
  const call = vi.spyOn(api, "trackRecord").mockResolvedValue(emptyTrackRecord);
  render(<PublishedTrackRecord />);
  await screen.findByText(/No published recommendations yet/);
  fireEvent.change(screen.getByLabelText("Published league"), {target:{value:"E0"}});
  fireEvent.change(screen.getByLabelText("Published market"), {target:{value:"BTTS"}});
  fireEvent.change(screen.getByLabelText(/Publication cutoff/), {target:{value:"2026-01-01T03:00:00+03:00"}});
  fireEvent.click(screen.getByText("Apply publication cutoff"));
  await screen.findByText(/No published recommendations yet/);
  expect(call.mock.lastCall?.[1]).toEqual({competition:"E0",market:"BTTS",asOf:"2026-01-01T00:00:00.000Z"});
  expect(screen.getByText(/does not prove which records were stored/)).toBeInTheDocument();
  expect(screen.getByText(/Missing records are not zero model performance/)).toBeInTheDocument();
  const count = call.mock.calls.length;
  fireEvent.change(screen.getByLabelText(/Publication cutoff/), {target:{value:"2026-01-01"}});
  fireEvent.click(screen.getByText("Apply publication cutoff"));
  expect(screen.getByRole("alert")).toHaveTextContent("timezone");
  expect(call.mock.calls.length).toBe(count);
});

it("renders EPL BTTS and corner results from the shared backend contract", async () => {
  const epl = (await import("../../tests/fixtures/epl_product_track_record.json")).default;
  vi.spyOn(api, "trackRecord").mockResolvedValue(decodeTrackRecord(epl));
  render(<PublishedTrackRecord />);
  expect(await screen.findByText("Both teams to score: No")).toBeInTheDocument();
  expect(screen.getAllByText("WIN").length).toBe(1);
  expect(screen.getByText("PENDING")).toBeInTheDocument();
  expect(screen.getByText(/E0 · Both teams to score · 1 published picks/)).toBeInTheDocument();
});

it("accepts London midnight only after rollover and sends the previous UTC date", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-24T22:59:59Z"));
  const call = vi.spyOn(api, "trackRecord").mockResolvedValue(emptyTrackRecord);
  render(<PublishedTrackRecord />);
  await screen.findByText(/No published recommendations yet/);
  fireEvent.change(screen.getByLabelText("Published league"), {target: {value: "E0"}});
  await waitFor(() => expect(call.mock.lastCall?.[1]?.competition).toBe("E0"));
  const calls = call.mock.calls.length;
  fireEvent.change(screen.getByLabelText(/Publication cutoff/), {target: {value: "2026-10-25T00:00:00+01:00"}});
  fireEvent.click(screen.getByText("Apply publication cutoff"));
  expect(screen.getByRole("alert")).toHaveTextContent("no later than now");
  expect(call).toHaveBeenCalledTimes(calls);

  clock.mockReturnValue(Date.parse("2026-10-24T23:00:01Z"));
  fireEvent.click(screen.getByText("Apply publication cutoff"));
  await waitFor(() => expect(call.mock.lastCall?.[1]).toEqual({competition: "E0", market: "", asOf: "2026-10-24T23:00:00.000Z"}));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps the two London autumn repeated-hour cutoffs distinct", async () => {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-25T02:00:00Z"));
  const call = vi.spyOn(api, "trackRecord").mockResolvedValue(emptyTrackRecord);
  render(<PublishedTrackRecord />);
  await screen.findByText(/No published recommendations yet/);
  for (const [input, expected] of [
    ["2026-10-25T01:30:00+01:00", "2026-10-25T00:30:00.000Z"],
    ["2026-10-25T01:30:00+00:00", "2026-10-25T01:30:00.000Z"],
  ]) {
    fireEvent.change(screen.getByLabelText(/Publication cutoff/), {target: {value: input}});
    fireEvent.click(screen.getByText("Apply publication cutoff"));
    await waitFor(() => expect(call.mock.lastCall?.[1]?.asOf).toBe(expected));
    expect(screen.getByText(`Recorded history as of ${expected}`)).toBeInTheDocument();
  }
  expect(call).toHaveBeenCalledTimes(3);
});
