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
    const view = render(<OpportunityDetailView id="demo-offer-upcoming" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading opportunity evidence");
    expect(await screen.findByText("Why it qualified")).toBeInTheDocument();
    expect(screen.getByText("Home venue observations")).toBeInTheDocument();
    expect(screen.getByText("Market at qualification")).toBeInTheDocument();
    expect(screen.getByText(/Recorded pre-kickoff snapshots/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Recorded market no-vig probability/ })).toBeInTheDocument();
    expect(screen.getByText("Market moved 5.0pp toward Zeno after qualification.")).toBeInTheDocument();
    expect(screen.getByText(/Zeno 62.0%/)).toBeInTheDocument();
    expect(screen.getByText(/-120 .*Market no-vig 57.0%/)).toBeInTheDocument();
    expect(view.container.querySelectorAll(".movement-point")).toHaveLength(2);
    expect(view.container.querySelectorAll(".movement-zeno")).toHaveLength(1);
    expect(screen.getAllByText(/Oct 1, 2099/).length).toBeGreaterThan(1);
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
  });
  it("shows legacy count absence and settled outcomes", async () => {
    vi.spyOn(api, "opportunityDetail").mockResolvedValue(mockOpportunityDetails[2]);
    const view = render(<OpportunityDetailView id="demo-offer-loss" />);
    expect(await screen.findByText(/Historical counts were not frozen/)).toBeInTheDocument();
    expect(screen.getByText("Validated LOSS")).toBeInTheDocument();
    expect(screen.getByText("No later pre-kickoff observation was recorded.")).toBeInTheDocument();
    expect(view.container.querySelectorAll(".movement-point")).toHaveLength(1);
  });
  it("shows away movement supplied by the backend without calculating it", async () => {
    const detail = structuredClone(mockOpportunityDetails[0]);
    detail.recorded_market[1].no_vig_market_probability = 0.48;
    detail.market_movement = { status: "AWAY_FROM_ZENO", market_change_percentage_points: -4,
      latest_comparable_observation_id: detail.recorded_market[1].observation_id };
    vi.spyOn(api, "opportunityDetail").mockResolvedValue(detail);
    render(<OpportunityDetailView id="demo-offer-upcoming" />);
    expect(await screen.findByText("Market moved 4.0pp away from Zeno after qualification.")).toBeInTheDocument();
  });
  it("labels a later inconsistent quote without concealing the opportunity", async () => {
    const detail = structuredClone(mockOpportunityDetails[0]);
    detail.recorded_market[1].decimal_odds = 5;
    detail.recorded_market[1].price_consistent = false;
    vi.spyOn(api, "opportunityDetail").mockResolvedValue(detail);
    render(<OpportunityDetailView id="demo-offer-upcoming" />);
    expect(await screen.findByText(/Provider price discrepancy/)).toBeInTheDocument();
    expect(screen.getByText("Why it qualified")).toBeInTheDocument();
    expect(screen.getByText(/Market no-vig 57.0%/)).toBeInTheDocument();
  });
  it("keeps an unpaired later quote visible and out of the plotted market", async () => {
    const detail = structuredClone(mockOpportunityDetails[0]);
    detail.recorded_market[1].no_vig_market_probability = null;
    detail.market_movement = { status: "UNAVAILABLE", market_change_percentage_points: null,
      latest_comparable_observation_id: null };
    vi.spyOn(api, "opportunityDetail").mockResolvedValue(detail);
    const view = render(<OpportunityDetailView id="demo-offer-upcoming" />);
    expect(await screen.findByText(/no later paired market was available/)).toBeInTheDocument();
    expect(screen.getByText(/Unpaired quote/)).toBeInTheDocument();
    expect(view.container.querySelectorAll(".movement-point")).toHaveLength(1);
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
