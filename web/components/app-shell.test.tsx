import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AppShell } from "./app-shell";

afterEach(cleanup);

describe("AppShell", () => {
  it("links the preserved analysis workspace at /analyze", () => {
    render(<AppShell active="Analyze"><p>Workspace</p></AppShell>);

    const analyze = screen.getByRole("link", { name: "Analyze" });
    expect(analyze).toHaveAttribute("href", "/analyze");
    expect(analyze).toHaveAttribute("aria-current", "page");
  });
});
