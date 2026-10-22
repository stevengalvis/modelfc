import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AppShell } from "./app-shell";

afterEach(cleanup);

describe("AppShell", () => {
  it("presents the Zeno FC brand and accessible home link", () => {
    render(<AppShell active="Predictions"><p>Workspace</p></AppShell>);

    expect(screen.getByRole("link", { name: "Zeno FC home" })).toHaveAttribute("href", "/");
    expect(screen.getByText("ZENO FC")).toBeInTheDocument();
    expect(screen.getByText("Z")).toHaveClass("brand-mark");
    expect(screen.getByText("CONTROL ROOM")).toBeInTheDocument();
  });

  it("links the preserved analysis workspace at /analyze", () => {
    render(<AppShell active="Analyze"><p>Workspace</p></AppShell>);

    const analyze = screen.getByRole("link", { name: "Analyze" });
    expect(analyze).toHaveAttribute("href", "/analyze");
    expect(analyze).toHaveAttribute("aria-current", "page");
  });

  it("links the BTTS research workspace without removing existing navigation", () => {
    render(<AppShell active="BTTS Value"><p>Workspace</p></AppShell>);
    expect(screen.getByRole("link", { name: "BTTS Value" })).toHaveAttribute("href", "/research/btts");
    expect(screen.getByRole("link", { name: "BTTS Value" })).toHaveAttribute("aria-current", "page");
    for (const label of ["Analyze", "Recommendations", "Predictions", "Teams", "Performance"])
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  });
});

it("organizes destinations into named groups while preserving every route", () => {
  render(<AppShell active="Next matches"><p>Workspace</p></AppShell>);
  const expected = {
    Upcoming: [["Next matches", "/upcoming"], ["Recommendations", "/recommendations"], ["Predictions", "/predictions"]],
    Performance: [["Performance", "/performance"]],
    Insights: [["Teams", "/teams"], ["BTTS Value", "/research/btts"], ["Analyze", "/analyze"]],
  };
  for (const [name, links] of Object.entries(expected)) {
    const group = screen.getByRole("group", { name });
    expect(Array.from(group.querySelectorAll("a")).map(a => [a.textContent, a.getAttribute("href")])).toEqual(links);
  }
});

it.each(["Next matches", "Recommendations", "Predictions", "Performance", "Teams", "BTTS Value", "Analyze"])(
  "marks only %s as the current destination", (active) => {
    render(<AppShell active={active}><p>Workspace</p></AppShell>);
    expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(screen.getByRole("link", { name: active })).toHaveAttribute("aria-current", "page");
  },
);
