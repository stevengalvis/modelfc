import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MarketMovement } from "./market-movement";
import { mockOpportunityDetails } from "../lib/api/mock";

afterEach(cleanup);

function threePoints(times: [string, string, string]) {
  const detail = structuredClone(mockOpportunityDetails[0]);
  detail.recorded_market[0].retrieved_at_utc = times[0];
  detail.recorded_market[1].retrieved_at_utc = times[1];
  detail.recorded_market.push({
    ...detail.recorded_market[1], observation_id: "third-recorded", retrieved_at_utc: times[2],
    no_vig_market_probability: 0.59,
  });
  detail.recorded_market_count = 3;
  detail.market_movement = { status: "TOWARD_ZENO", market_change_percentage_points: 7,
    latest_comparable_observation_id: "third-recorded" };
  return detail;
}

function pointXs(container: HTMLElement) {
  return Array.from(container.querySelectorAll(".movement-point"), (point) => Number(point.getAttribute("cx")));
}

describe("recorded market chart", () => {
  it("spaces hours in proportion to elapsed UTC time and uses concise same-day labels", () => {
    const detail = threePoints([
      "2099-10-10T10:00:00Z", "2099-10-10T11:00:00Z", "2099-10-10T13:00:00Z",
    ]);
    const { container } = render(<MarketMovement detail={detail} />);
    const [first, second, third] = pointXs(container);
    expect((second - first) / (third - first)).toBeCloseTo(1 / 3);
    expect(screen.getByText("10:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("13:00 UTC")).toBeInTheDocument();
    expect(container.querySelectorAll(".movement-label")).toHaveLength(2);
    expect(Array.from(container.querySelectorAll(".movement-snapshots li span"), (item) => item.textContent))
      .toContain("Oct 10, 2099, 11:00 AM UTC");
  });

  it("preserves a multi-day gap while labeling both UTC dates", () => {
    const detail = threePoints([
      "2099-10-05T10:00:00Z", "2099-10-10T11:00:00Z", "2099-10-10T12:00:00Z",
    ]);
    const { container } = render(<MarketMovement detail={detail} />);
    const [first, second, third] = pointXs(container);
    expect(second - first).toBeGreaterThan((third - second) * 100);
    expect((second - first) / (third - first)).toBeCloseTo(121 / 122);
    expect(screen.getByText(/Oct 5, 10:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/Oct 10, 12:00 UTC/)).toBeInTheDocument();
    expect(Array.from(container.querySelectorAll(".movement-snapshots li span"), (item) => item.textContent))
      .toEqual(["Oct 5, 2099, 10:00 AM UTC", "Oct 10, 2099, 11:00 AM UTC", "Oct 10, 2099, 12:00 PM UTC"]);
    expect(container.querySelectorAll(".movement-market")).toHaveLength(2);
  });

  it("centers one qualification point without fabricating time or movement", () => {
    const detail = structuredClone(mockOpportunityDetails[1]);
    const { container } = render(<MarketMovement detail={detail} />);
    expect(pointXs(container)).toEqual([300]);
    expect(container.querySelectorAll(".movement-market")).toHaveLength(0);
    expect(container.querySelectorAll(".movement-label")).toHaveLength(1);
    expect(screen.getByText("No later pre-kickoff observation was recorded.")).toBeInTheDocument();
    expect(screen.getByText("Qualified")).toBeInTheDocument();
  });

  it("labels actual probabilities, the nearby reference, and recorded points", () => {
    const detail = structuredClone(mockOpportunityDetails[0]);
    const { container } = render(<MarketMovement detail={detail} />);
    expect(screen.getByText("Market 52.0%")).toBeInTheDocument();
    expect(screen.getByText("Market 57.0%")).toBeInTheDocument();
    expect(screen.getByText("Zeno 62.0%")).toBeInTheDocument();
    expect(screen.getByText("Qualified")).toBeInTheDocument();
    expect(screen.getByText("Recorded")).toBeInTheDocument();
    const reference = container.querySelector(".movement-zeno")!;
    const label = container.querySelector(".movement-reference-label")!;
    expect(Number(reference.getAttribute("y1")) - Number(label.getAttribute("y"))).toBe(12);
    expect(screen.getByText(/Recorded pre-kickoff snapshots. Not live or closing odds/)).toBeInTheDocument();
    expect(screen.getByText(/Lines join recorded points only; gaps are not filled/)).toBeInTheDocument();
  });
});
