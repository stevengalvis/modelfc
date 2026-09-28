import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockOpportunities, mockPerformance, mockPredictions } from "../lib/api/mock";
import { api } from "../lib/api/client";
import { PredictionsDashboard, PerformanceDashboard } from "./prospective-dashboard";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
describe("prospective dashboards", () => {
  it("shows loading, prediction details and settled opportunities", async () => {
    let resolve!: (value: typeof mockPredictions) => void;
    vi.spyOn(api, "predictions").mockImplementation(() => new Promise((done) => { resolve = done; }));
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    render(<PredictionsDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading prospective evidence");
    await waitFor(() => expect(api.predictions).toHaveBeenCalled());
    resolve(mockPredictions);
    expect(await screen.findByText("Prediction runs")).toBeInTheDocument();
    expect(screen.getByText("WIN")).toBeInTheDocument();
    expect(screen.getByText("LOSS")).toBeInTheDocument();
    expect(screen.getByText("PUSH")).toBeInTheDocument();
    expect(screen.getAllByText("62.0%")).toHaveLength(4);
  });
  it("shows an empty state", async () => {
    vi.spyOn(api, "predictions").mockResolvedValue([]);
    vi.spyOn(api, "opportunities").mockResolvedValue([]);
    render(<PredictionsDashboard />);
    expect(await screen.findByText("No prospective predictions recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No qualifying opportunities recorded yet.")).toBeInTheDocument();
  });
  it("shows API errors without mock data", async () => {
    vi.spyOn(api, "predictions").mockRejectedValue(new Error("API down"));
    vi.spyOn(api, "opportunities").mockResolvedValue([]);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("API down"));
    expect(screen.queryByText("Prediction runs")).not.toBeInTheDocument();
  });
  it("rejects an opportunity with no matching prediction", async () => {
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    vi.spyOn(api, "predictions").mockResolvedValue([]);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("inconsistent predictions and opportunities"));
    expect(screen.queryByText("Market opportunities")).not.toBeInTheDocument();
  });
  it("rejects a settled opportunity that contradicts the prediction score", async () => {
    const predictions = structuredClone(mockPredictions);
    predictions[1].actual_home_corners = 7;
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    vi.spyOn(api, "predictions").mockResolvedValue(predictions);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("inconsistent predictions and opportunities"));
  });
  it("rejects counts from split prospective snapshots", async () => {
    const predictions = structuredClone(mockPredictions);
    predictions[0].opportunity_count = 2;
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    vi.spyOn(api, "predictions").mockResolvedValue(predictions);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Retry to read a consistent snapshot"));
  });
  it("renders backend performance values", async () => {
    vi.spyOn(api, "performance").mockResolvedValue(mockPerformance);
    render(<PerformanceDashboard />);
    expect(await screen.findByText("Realized profit, units")).toBeInTheDocument();
    expect(screen.getByText("-0.17")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
  });
});
