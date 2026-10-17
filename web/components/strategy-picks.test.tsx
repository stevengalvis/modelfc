import { cleanup,fireEvent,render,screen } from "@testing-library/react";
import { afterEach,expect,it,vi } from "vitest";
import { api } from "@/lib/api/client";
import { decodeStrategyPage,emptyTrackRecord } from "@/lib/api/track-record";
import fixture from "../../tests/fixtures/strategy_picks.json";
import { RecommendationsDashboard } from "./recommendations-dashboard";
import { PerformanceDashboard } from "./prospective-dashboard";
import { StrategyPicks } from "./strategy-picks";
afterEach(()=>{cleanup();vi.restoreAllMocks();});
const empty=()=>({...decodeStrategyPage(fixture),publications:[],total:0,next_offset:null,summary:emptyTrackRecord.summary,totals:emptyTrackRecord.summary,market_totals:[],by_league_market:[],profit_dollars:0,total_profit_dollars:0});
it("loads only current picks on Recommendations and labels simulated flat risk",async()=>{
 const call=vi.spyOn(api,"strategyPicks").mockResolvedValue(decodeStrategyPage(fixture));
 const analysis=vi.spyOn(api,"shortlist"); const history=vi.spyOn(api,"performance");
 render(<RecommendationsDashboard/>);
 await screen.findByText("Both teams to score: Yes");
 expect(call.mock.calls[0][0].scope).toBe("current");
 expect(analysis).not.toHaveBeenCalled();expect(history).not.toHaveBeenCalled();
 expect(screen.getByText(/Simulated flat \$2 risked/)).toBeInTheDocument();
});
it("filters reset the query and results remain separate from model accuracy",async()=>{
 const call=vi.spyOn(api,"strategyPicks").mockResolvedValue(empty());
 const accuracy=vi.spyOn(api,"performance").mockImplementation(()=>new Promise(()=>{}));
 render(<PerformanceDashboard/>);
 await screen.findByText(/No published picks/); expect(accuracy).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("tab",{name:"Higher Probability"}));
 await screen.findByText(/No published picks/);
 expect(call.mock.lastCall?.[0].strategy).toBe("higher_probability");
 fireEvent.change(screen.getByLabelText("Market"),{target:{value:"BTTS"}});
 await screen.findByText(/No published picks/);
 expect(call.mock.lastCall?.[0]).toMatchObject({market:"BTTS",offset:0});
 fireEvent.click(screen.getByRole("tab",{name:"Model Accuracy"}));
 expect(accuracy).toHaveBeenCalledTimes(1);
});
it("never treats an error as empty and retries",async()=>{
 vi.spyOn(api,"strategyPicks").mockRejectedValueOnce(new Error()).mockResolvedValue(empty());
 render(<StrategyPicks scope="history"/>);
 await screen.findByRole("alert");expect(screen.queryByText(/No published picks/)).not.toBeInTheDocument();
 fireEvent.click(screen.getByText("Retry strategy picks"));await screen.findByText(/No published picks/);
});
it("paginates only on user action",async()=>{
 const page={...decodeStrategyPage(fixture),total:6,next_offset:5};
 const call=vi.spyOn(api,"strategyPicks").mockResolvedValueOnce(page).mockResolvedValue(empty());
 render(<StrategyPicks scope="history"/>);await screen.findByText("Both teams to score: Yes");
 expect(call).toHaveBeenCalledTimes(1);fireEvent.click(screen.getByText("Next picks"));
 await screen.findByText(/No published picks/);expect(call.mock.lastCall?.[0].offset).toBe(5);
});
it("renders reviewed simulation void accounting without calling it bookmaker grading",async()=>{
 const page=decodeStrategyPage(fixture);
 const row=page.publications[0];
 row.result="SIMULATION_VOID";row.profit_units=0;row.profit_dollars=0;
 row.simulation_void={policy:"reviewed-simulation-void-v1",record_hash:"c".repeat(64),reason:"Reviewed cancellation",recorded_at_utc:row.published_at_utc};
 const summary={...page.summary,settled:0,wins:0,losses:0,pushes:0,pending:0,voids:1,profit_units:0,win_rate:null,roi:null};
 const value={...page,simulation_void_supported:true,publications:[row],total:1,next_offset:null,
  summary,totals:summary,profit_dollars:0,total_profit_dollars:0,
  market_totals:page.market_totals.map(g=>({...g,summary})),by_league_market:page.by_league_market.map(g=>({...g,summary}))};
 vi.spyOn(api,"strategyPicks").mockResolvedValue(decodeStrategyPage(value));
 render(<StrategyPicks scope="history"/>);
 expect(await screen.findByText(/1 voided picks/)).toBeInTheDocument();
 expect(screen.getByText(/ROI: —/)).toHaveTextContent("$0.00");
 expect(screen.getByText(/Voided pick · simulated/)).toBeInTheDocument();
 expect(screen.getByText(/do not confirm a sportsbook refund/)).toBeInTheDocument();
 expect(screen.getByText(/Simulated stake returned; review/)).toHaveTextContent("Reviewed cancellation");
 expect(()=>decodeStrategyPage({...value,simulation_void_supported:false})).toThrow();
 expect(()=>decodeStrategyPage({...value,totals:{...summary,settled:1}})).toThrow();
});
