import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import { mockAnalyze, mockCapabilities } from "@/lib/api/mock";
import type { AnalysisRequest, AnalysisResponse } from "@/lib/api/types";
import { AnalyzeWorkspace } from "./analyze-workspace";

const sportsbookText = `Championship
2026-09-17
Birmingham vs Millwall
Birmingham team corners O4 -110`;

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function pasteAndParse(text = sportsbookText) {
  fireEvent.change(screen.getByRole("textbox", { name: /sportsbook fixture/i }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /parse lines/i }));
  await act(async () => {});
}

async function analyzeDemo() {
  await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
  await screen.findByText("Analysis complete");
}

describe("AnalyzeWorkspace", () => {
  it("corrects canonical teams and fields without rewriting the original paste", async () => {
    const analyze = vi.spyOn(api, "analyze");
    render(<AnalyzeWorkspace />);
    await pasteAndParse(sportsbookText.replaceAll("Birmingham", "Birmingham City").replace("O4", "O4.5"));
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
    expect(screen.getByText(/not an eligible canonical home team/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Home team"), { target: { value: "Birmingham" } });
    fireEvent.change(screen.getByLabelText("Market 1 line"), { target: { value: "4" } });
    await analyzeDemo();
    expect(analyze.mock.calls[0][0].fixture.home_team).toBe("Birmingham");
    expect(analyze.mock.calls[0][0].markets[0].line).toBe(4);
    expect((screen.getByRole("textbox", { name: /sportsbook fixture/i }) as HTMLTextAreaElement).value).toContain("Birmingham City");
  });

  it("rejects impossible calendar dates and empty numeric fields", async () => {
    render(<AnalyzeWorkspace />);
    await pasteAndParse(sportsbookText.replace("2026-09-17", "2026-02-30"));
    expect(screen.getByText(/enter a real date/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Market 1 line"), { target: { value: "" } });
    expect(screen.getByText("Line is required.")).toBeInTheDocument();
  });

  it("blocks a canonical team that is eligible only for the opposite venue", async () => {
    const caps = structuredClone(mockCapabilities);
    caps.competitions[0].teams_by_side = { HOME: ["Birmingham"], AWAY: ["Millwall"] };
    vi.spyOn(api, "capabilities").mockResolvedValue(caps);
    render(<AnalyzeWorkspace />);
    await pasteAndParse(sportsbookText.replace("Birmingham vs Millwall", "Millwall vs Birmingham"));
    expect(screen.getByText(/Millwall has insufficient home venue history/)).toBeInTheDocument();
    expect(screen.getByText(/Birmingham has insufficient away venue history/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
  });

  it("shows SP2 warnings and null refresh fields without advertising availability", async () => {
    render(<AnalyzeWorkspace />);
    await pasteAndParse(sportsbookText.replace("Championship", "SP2"));
    expect(screen.getByText("Competition unavailable")).toBeInTheDocument();
    expect(screen.getByText(/La Liga 2 corner history and its refresh path have not been verified/)).toBeInTheDocument();
    expect(screen.getAllByText("Not recorded")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
  });

  it("excludes match totals visibly and analyzes every supported row in a mixed paste", async () => {
    const analyze = vi.spyOn(api, "analyze");
    render(<AnalyzeWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "Mixed board example" }));
    fireEvent.click(screen.getByRole("button", { name: "Parse lines" }));
    await analyzeDemo();
    expect(screen.getByText(/Not analyzed: HISTORICAL_EVALUATION_REQUIRED/)).toBeInTheDocument();
    expect(screen.getByText("Total O9.5 +105")).toBeInTheDocument();
    expect(analyze.mock.calls[0][0].markets).toHaveLength(1);
    expect(analyze.mock.calls[0][0].markets[0].market_type).toBe("TEAM_TOTAL");
    expect(screen.getByText(/Latest usable history is 17 days/)).toBeInTheDocument();
    expect(screen.getByText(/TEAM_VENUE_HISTORY_AGE: Latest Birmingham home/)).toBeInTheDocument();
  });

  it("blocks an all-unsupported batch", async () => {
    render(<AnalyzeWorkspace />);
    await pasteAndParse(sportsbookText.replace("Birmingham team corners O4 -110", "Total O9.5 +105"));
    expect(screen.getByText("0 of 1 markets ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
  });

  it("shows raw win, push, decisive probability and the disabled-logging reason", async () => {
    render(<AnalyzeWorkspace />);
    await pasteAndParse();
    await analyzeDemo();
    expect(screen.getByRole("columnheader", { name: "Raw win" })).toBeInTheDocument();
    expect(screen.getByText("60.2%")).toBeInTheDocument();
    expect(screen.getByText("16.0%")).toBeInTheDocument();
    expect(screen.getByText("71.6%")).toBeInTheDocument();
    expect(screen.getByText(/backend disabled logging: UNTRUSTED_KICKOFF/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log selected (0)" })).toBeDisabled();
  });

  it("supports zero, one, many, all, clears selection and invalidates after editing", async () => {
    render(<AnalyzeWorkspace />);
    await pasteAndParse(sportsbookText + "\nBirmingham O4 -110\nBirmingham O4 -110");
    await analyzeDemo();
    const checkboxes = screen.getAllByRole("checkbox");
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    fireEvent.click(checkboxes[0]);
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(checkboxes[1]);
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /select all supported/i }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    fireEvent.click(checkboxes[0]);
    fireEvent.change(screen.getByLabelText("Market 1 line"), { target: { value: "4.5" } });
    expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument();
  });

  it("ignores a delayed response after the input changes", async () => {
    let resolveRequest!: (value: AnalysisResponse) => void;
    let captured!: AnalysisRequest;
    vi.spyOn(api, "analyze").mockImplementation((request) => {
      captured = request;
      return new Promise((resolve) => { resolveRequest = resolve; });
    });
    render(<AnalyzeWorkspace />);
    await pasteAndParse();
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    fireEvent.change(screen.getByLabelText("Fixture date"), { target: { value: "2026-09-21" } });
    await act(async () => { resolveRequest(await mockAnalyze(captured)); });
    expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument();
  });

  it("reuses retry keys, then starts a fresh request and selection after success", async () => {
    const analyze = vi.spyOn(api, "analyze")
      .mockRejectedValueOnce(new ModelFCApiError("Temporary failure", "TEMPORARY", true, 503))
      .mockImplementation(mockAnalyze);
    render(<AnalyzeWorkspace />);
    await pasteAndParse();
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await screen.findByText(/TEMPORARY: Temporary failure/i);
    await analyzeDemo();
    expect(analyze.mock.calls[1][0].idempotency_key).toBe(analyze.mock.calls[0][0].idempotency_key);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await waitFor(() => expect(analyze).toHaveBeenCalledTimes(3));
    await screen.findByText("0 selected");
    expect(analyze.mock.calls[2][0].idempotency_key).not.toBe(analyze.mock.calls[1][0].idempotency_key);
  });

  it.each([
    ["DATA_SOURCE_UNAVAILABLE", "Data unavailable"],
    ["INSUFFICIENT_HISTORY", "Insufficient history"],
    ["INVALID_REQUEST", "Input validation failed"],
  ])("distinguishes %s from transport failures", async (code, title) => {
    vi.spyOn(api, "analyze").mockRejectedValue(new ModelFCApiError("Backend detail", code, false, 422));
    render(<AnalyzeWorkspace />);
    await pasteAndParse();
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument();
  });

  it("blocks analysis when capabilities fail and allows an explicit connection retry", async () => {
    vi.spyOn(api, "capabilities").mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(mockCapabilities);
    render(<AnalyzeWorkspace />);
    await screen.findByText("API connection failed");
    await pasteAndParse();
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
  });
});
