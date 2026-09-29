import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OpportunityDetailView } from "./opportunity-detail";
import { api } from "../lib/api/client";
import { mockOpportunityDetails } from "../lib/api/mock";
import { ModelFCApiError } from "../lib/api/errors";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Opportunity Detail", () => {
  it("loads recorded context, paired market and later snapshots", async () => {
    vi.spyOn(api, "opportunityDetail").mockResolvedValue(mockOpportunityDetails[0]);
    render(<OpportunityDetailView id="demo-offer-upcoming" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading opportunity evidence");
    expect(await screen.findByText("Why it qualified")).toBeInTheDocument();
    expect(screen.getByText("Home venue observations")).toBeInTheDocument();
    expect(screen.getByText("Market at qualification")).toBeInTheDocument();
    expect(screen.getByText(/Recorded pre-kickoff snapshots/)).toBeInTheDocument();
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
  });
  it("shows legacy count absence and settled outcomes", async () => {
    vi.spyOn(api, "opportunityDetail").mockResolvedValue(mockOpportunityDetails[2]);
    render(<OpportunityDetailView id="demo-offer-loss" />);
    expect(await screen.findByText(/Historical counts were not frozen/)).toBeInTheDocument();
    expect(screen.getByText("Validated LOSS")).toBeInTheDocument();
  });
  it("distinguishes a missing ID from an unavailable API and never shows mock fallback", async () => {
    const method = vi.spyOn(api, "opportunityDetail").mockRejectedValue(new ModelFCApiError("Opportunity was not found.", "OPPORTUNITY_NOT_FOUND", false, 404));
    const view = render(<OpportunityDetailView id="missing" />);
    expect(await screen.findByText("Opportunity not found")).toBeInTheDocument();
    view.unmount();
    method.mockRejectedValue(new ModelFCApiError("Prospective evidence failed validation.", "LEDGER_INTEGRITY_FAILURE", false, 409));
    render(<OpportunityDetailView id="bad-evidence" />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("LEDGER_INTEGRITY_FAILURE"));
    expect(screen.queryByText("Why it qualified")).not.toBeInTheDocument();
  });
});
