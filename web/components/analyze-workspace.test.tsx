import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnalyzeWorkspace } from "./analyze-workspace";

const sportsbookText = `Championship
2026-09-20
Coventry City vs Birmingham City
Coventry City team corners O4.5 -145
Coventry City team corners O5.5 +105
Birmingham City team corners O3.5 -120
Match total corners U10.5 -125`;

function pasteAndParse() {
  fireEvent.change(screen.getByRole("textbox", { name: /sportsbook fixture/i }), { target: { value: sportsbookText } });
  fireEvent.click(screen.getByRole("button", { name: /parse lines/i }));
}

describe("AnalyzeWorkspace", () => {
  it("parses pasted sportsbook text into a simple review", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    expect(screen.getByText("Championship")).toBeInTheDocument();
    expect(screen.getByText("Fixture").parentElement).toHaveTextContent("Coventry City vs Birmingham City");
    expect(screen.getByText("4 markets parsed")).toBeInTheDocument();
    expect(screen.getByText("Coventry City corners O4.5 -145")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
  });

  it("submits every parsed market and shows supported and unsupported results", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    expect(await screen.findByText("Analysis complete")).toBeInTheDocument();
    expect(screen.getAllByText("Supported")).toHaveLength(3);
    expect(screen.getByText("Not supported yet")).toBeInTheDocument();
  });

  it("selects only supported analysis rows", async () => {
    render(<AnalyzeWorkspace />);
    pasteAndParse();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await screen.findByText("Analysis complete");
    fireEvent.click(screen.getByRole("button", { name: /select all supported/i }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log selected (3)" })).toBeDisabled();
  });

  it("recognizes La Liga 2 instead of La Liga", async () => {
    render(<AnalyzeWorkspace />);
    fireEvent.change(screen.getByRole("textbox", { name: /sportsbook fixture/i }), { target: { value: sportsbookText.replace("Championship", "La Liga 2") } });
    fireEvent.click(screen.getByRole("button", { name: /parse lines/i }));
    expect(screen.getByText("La Liga 2")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /analyze all/i })).toBeEnabled());
  });
});
