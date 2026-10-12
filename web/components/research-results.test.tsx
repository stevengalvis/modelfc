import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../../tests/fixtures/research_settled.json";
import { decodePredictions } from "../lib/api/prospective";
import { api } from "../lib/api/client";
import { PredictionsDashboard } from "./prospective-dashboard";
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it("renders independent research corner and BTTS final results with evidence",async()=>{
  vi.spyOn(api,"predictions").mockResolvedValue(decodePredictions(fixture));
  vi.spyOn(api,"opportunities").mockResolvedValue([]);
  render(<PredictionsDashboard/>);
  expect(await screen.findByText("Final goals")).toBeInTheDocument();
  expect(screen.getByText("Actual corners")).toBeInTheDocument();
  expect(screen.getByText("BTTS result")).toBeInTheDocument();
  expect(screen.getByText("YES")).toBeInTheDocument();
  expect(screen.getAllByText("Result evidence")).toHaveLength(2);
});
it("rejects contradictory BTTS grade and missing settled provenance",()=>{
  const bad=structuredClone(fixture);bad.find(r=>r.prediction_type==="BTTS")!.actual_btts="NO";
  expect(()=>decodePredictions(bad)).toThrow();
  const missing=structuredClone(fixture);Object.assign(missing[0], {result_source_sha256:null});
  expect(()=>decodePredictions(missing)).toThrow();
});
