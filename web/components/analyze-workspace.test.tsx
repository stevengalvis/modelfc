import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { ModelFCApiError } from "@/lib/api/errors";
import { mockAnalyze } from "@/lib/api/mock";
import type { AnalysisRequest, AnalysisResponse } from "@/lib/api/types";
import { AnalyzeWorkspace } from "./analyze-workspace";

const sportsbookText = `Championship
2026-09-20
Coventry City vs Birmingham City
Coventry City O4 -145
Opponent O3.5 -120
Total U10 -125`;

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function pasteAndParse(text = sportsbookText) {
  fireEvent.change(screen.getByRole("textbox", { name: /sportsbook fixture/i }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /parse lines/i }));
}

async function analyzeDemo() {
  await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
  await screen.findByText("Analysis complete");
}

describe("AnalyzeWorkspace", () => {
  it("parses shorthand into compact editable fixture and market controls", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    expect(screen.getByDisplayValue("E1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Coventry City")).toBeInTheDocument();
    expect(screen.getByText("Opponent O3.5 -120")).toBeInTheDocument();
    expect(screen.getByDisplayValue("10")).toBeInTheDocument();
    expect(screen.getByText("3 of 3 markets ready")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
  });

  it("rejects impossible calendar dates and empty numeric fields", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse(sportsbookText.replace("2026-09-20", "2026-02-30"));
    await screen.findByText(/demo responses use a fixed fixture/i);
    expect(screen.getByText(/enter a real date/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Market 1 line"), { target: { value: "" } });
    expect(screen.getByText("Line is required.")).toBeInTheDocument();
  });

  it("shows La Liga 2 as recognized but unavailable from backend capabilities", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse(sportsbookText.replace("Championship", "SP2"));
    expect(await screen.findByText("Competition not ready")).toBeInTheDocument();
    expect(screen.getByText(/recognized in pasted text, but backend data is not configured/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /analyze all/i })).toBeDisabled();
  });

  it("shows backend warnings and whole-line raw, push, and decisive probabilities", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    await analyzeDemo();
    expect(screen.getByText("Analysis warnings")).toBeInTheDocument();
    expect(screen.getByText(/latest history is 6 days/i)).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Raw win" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Push" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Decisive" })).toBeInTheDocument();
    expect(screen.getByText("13.9%")).toBeInTheDocument();
    expect(screen.getByText(/backend disabled logging: UNTRUSTED_KICKOFF/i)).toBeInTheDocument();
  });

  it("supports zero, one, many, all and resets results after an edit", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    await analyzeDemo();
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select Coventry City O4"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /select all supported/i }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("0 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /select all supported/i }));
    fireEvent.change(screen.getByLabelText("Market 1 line"), { target: { value: "4.5" } });
    expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument();
  });

  it("ignores a delayed response after the input changes", async () => {
    let resolveRequest: ((value: AnalysisResponse) => void) | undefined;
    let captured: AnalysisRequest | undefined;
    vi.spyOn(api, "analyze").mockImplementation((request) => {
      captured = request;
      return new Promise((resolve) => { resolveRequest = resolve; });
    });
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await waitFor(() => expect(captured).toBeDefined());
    fireEvent.change(screen.getByLabelText("Fixture date"), { target: { value: "2026-09-21" } });
    resolveRequest?.(await mockAnalyze(captured!));
    await waitFor(() => expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument());
  });

  it("reuses an idempotency key only when retrying the same failed request", async () => {
    const keys: string[] = [];
    vi.spyOn(api, "analyze")
      .mockImplementationOnce(async (request) => {
        keys.push(request.idempotency_key);
        throw new ModelFCApiError("Temporary failure", "TEMPORARY", true, 503);
      })
      .mockImplementationOnce(async (request, signal) => {
        keys.push(request.idempotency_key);
        return mockAnalyze(request, signal);
      });
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await screen.findByText(/TEMPORARY: Temporary failure/i);
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await screen.findByText("Analysis complete");
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });
});
