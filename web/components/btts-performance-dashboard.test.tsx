import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { BttsPerformanceDashboard } from "./btts-performance-dashboard";
import { decodeBttsPerformance, emptyBttsPerformance } from "@/lib/api/btts-performance";
import fixture from "../../tests/fixtures/btts_performance.json";

afterEach(() => vi.restoreAllMocks());
describe("BTTS performance rendered states", () => {
  it("renders outcomes, sample labels and provenance", async () => {
    vi.spyOn(api,"bttsPerformance").mockResolvedValue(decodeBttsPerformance(fixture));
    render(<BttsPerformanceDashboard />);
    expect(await screen.findByText(/1 unique fixtures/)).toBeInTheDocument();
    expect(screen.getByText(/Scored sample: n=1/)).toBeInTheDocument();
    expect(screen.getByText("2–1 · YES")).toBeInTheDocument();
    expect(screen.getByText(/Small samples are unstable/)).toBeInTheDocument();
    expect(screen.getByText(fixture.forecasts[0].forecast_id)).toBeInTheDocument();
    expect(screen.getByRole("link",{name:"Corner performance"})).toHaveAttribute("href","/performance");
  });
  it("renders empty state and missing metrics", async () => {
    vi.spyOn(api,"bttsPerformance").mockResolvedValue(emptyBttsPerformance);
    render(<BttsPerformanceDashboard />);
    expect(await screen.findByText(/No frozen Championship/)).toBeInTheDocument();
    expect(screen.getByText(/Brier score: Not available/)).toBeInTheDocument();
  });
  it("renders unresolved results without implying No", async () => {
    const value = structuredClone(decodeBttsPerformance(fixture));
    value.forecasts[0] = {...value.forecasts[0],status:"UNRESOLVED",actual_btts:null,home_goals:null,away_goals:null,
      outcome_id:null,recorded_at_utc:null,result_provider:null,result_source_sha256:null};
    value.settled_fixtures=0; value.unresolved_fixtures=1; value.metrics=emptyBttsPerformance.metrics;
    vi.spyOn(api,"bttsPerformance").mockResolvedValue(decodeBttsPerformance(value));
    render(<BttsPerformanceDashboard />);
    expect(await screen.findByText("Unresolved — result missing")).toBeInTheDocument();
    expect(screen.queryByText("2–1 · YES")).not.toBeInTheDocument();
  });
  it("retries failures and shows loading", async () => {
    const call=vi.spyOn(api,"bttsPerformance").mockRejectedValueOnce(new Error("private path"))
      .mockResolvedValueOnce(emptyBttsPerformance);
    render(<BttsPerformanceDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByRole("alert")).not.toHaveTextContent("private path");
    fireEvent.click(screen.getByRole("button",{name:"Retry"}));
    await waitFor(() => expect(call).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/No frozen Championship/)).toBeInTheDocument();
  });
});
it("shows reviewed correction provenance without replacing the frozen forecast",async()=>{
 const value=decodeBttsPerformance(fixture);
 value.forecasts[0].corrections=[{correction_id:"b".repeat(64),supersedes_record_hash:"a".repeat(64),record_hash:"c".repeat(64),
  reason:"Reviewed official correction",recorded_at_utc:value.forecasts[0].recorded_at_utc!,result:{home_goals:2,away_goals:1}}];
 vi.spyOn(api,"bttsPerformance").mockResolvedValue(decodeBttsPerformance(value));
 render(<BttsPerformanceDashboard/>);
 expect(await screen.findByText(/Reviewed official correction/)).toHaveTextContent("Original forecast and outcome retained");
 expect(screen.getByText(value.forecasts[0].forecast_id)).toBeInTheDocument();
});

it("uses server cohorts for scope and historical filters and exposes unavailable EPL", async () => {
  const call = vi.spyOn(api,"bttsPerformance").mockImplementation(async (_signal, filters) => ({
    ...structuredClone(emptyBttsPerformance), competition:filters?.competition ?? "E1",
    coverage:filters?.competition === "E0" ? "UNSUPPORTED" : filters?.competition === "ALL" ? "PARTIAL" : "SUPPORTED",
    as_of_utc:filters?.asOf || emptyBttsPerformance.as_of_utc,
  }));
  render(<BttsPerformanceDashboard />);
  await screen.findByText(/No frozen Championship/);
  fireEvent.change(screen.getByRole("combobox",{name:"League scope"}),{target:{value:"ALL"}});
  expect(await screen.findByText(/Overall coverage is partial/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/As of \(ISO/),{target:{value:"2026-09-19T12:00:00Z"}});
  fireEvent.click(screen.getByRole("button",{name:"Apply history cutoff"}));
  await waitFor(() => expect(call).toHaveBeenLastCalledWith(expect.any(AbortSignal),{competition:"ALL",asOf:"2026-09-19T12:00:00.000Z"}));
  expect(await screen.findByText(/As of 2026-09-19T12:00:00.000Z/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox",{name:"League scope"}),{target:{value:"E0"}});
  expect(await screen.findByText(/This is not zero EPL performance/)).toBeInTheDocument();
  expect(screen.getByText(/Brier score: Not available/)).toBeInTheDocument();
  expect(call).toHaveBeenLastCalledWith(expect.any(AbortSignal),{competition:"E0",asOf:"2026-09-19T12:00:00.000Z"});
  const count = call.mock.calls.length;
  fireEvent.change(screen.getByLabelText(/As of \(ISO/),{target:{value:"2026-09-19T12:00:00"}});
  fireEvent.click(screen.getByRole("button",{name:"Apply history cutoff"}));
  expect(screen.getByRole("alert")).toHaveTextContent("timezone");
  expect(call).toHaveBeenCalledTimes(count);
});
