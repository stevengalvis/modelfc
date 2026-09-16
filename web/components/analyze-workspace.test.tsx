import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnalyzeWorkspace } from "./analyze-workspace";

describe("AnalyzeWorkspace", () => {
  it("submits all market rows and shows supported and unsupported results", async () => {
    render(<AnalyzeWorkspace />);
    await waitFor(() => expect(screen.getByText(/La Liga/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    expect(await screen.findByText("Analysis complete")).toBeInTheDocument();
    expect(screen.getAllByText("Supported")).toHaveLength(3);
    expect(screen.getByText("Not supported yet")).toBeInTheDocument();
  });

  it("supports adding and removing market rows", async () => {
    render(<AnalyzeWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: /add market/i }));
    expect(screen.getByText("5 markets ready")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove market 5" }));
    expect(screen.getByText("4 markets ready")).toBeInTheDocument();
  });

  it("selects only supported analysis rows", async () => {
    render(<AnalyzeWorkspace />);
    await waitFor(() => expect(screen.getByText(/La Liga/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /analyze all/i }));
    await screen.findByText("Analysis complete");
    fireEvent.click(screen.getByRole("button", { name: /select all supported/i }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log selected (3)" })).toBeDisabled();
  });
});
