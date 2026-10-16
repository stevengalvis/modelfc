import { expect,it } from "vitest";
import { decodeStrategyPage } from "./track-record";
import fixture from "../../../tests/fixtures/strategy_picks.json";
it("validates independent strategy, page and dollar math",()=>{
 expect(decodeStrategyPage(fixture).stake_dollars).toBe(2);
 for(const mutation of [
  (v:typeof fixture)=>{v.stake_dollars=5;},
  (v:typeof fixture)=>{v.total_profit_dollars=10;},
  (v:typeof fixture)=>{v.publications[0].strategy="higher_probability";},
  (v:typeof fixture)=>{Object.assign(v,{next_offset:5});},
 ]){const v=structuredClone(fixture);mutation(v);expect(()=>decodeStrategyPage(v)).toThrow();}
});
