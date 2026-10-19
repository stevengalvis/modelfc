import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
