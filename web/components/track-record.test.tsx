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
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "higher_probability" } });
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
