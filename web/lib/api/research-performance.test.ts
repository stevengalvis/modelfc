import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiClient } from './client';
import { decodeResearchPerformance, defaultResearchFilters as filters } from './research-performance';
import fixture from '../../../tests/fixtures/research_performance.json';
afterEach(()=>vi.restoreAllMocks());
describe('saved research history contract',()=>{
 it('preserves separate fixture, forecast and score denominators',()=>{
  const r=decodeResearchPerformance(fixture,filters);
  expect(r.unique_fixtures).toBe(1);expect(r.forecast_cohorts).toBe(2);
  expect(r.markets[0].probability_scores.brier_score).toBeNull();
  expect(r.markets[1].probability_scores.sample_count).toBe(1);
 });
 it('uses identical selection on the installed no-store endpoint',async()=>{
  const f={...filters,asOf:fixture.selection.as_of_utc};
  const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(fixture)));
  await createApiClient('live','https://example.test/api/v1').researchPerformance(undefined,f);
  const url=new URL(String(fetcher.mock.calls[0][0]));
  expect(url.pathname).toBe('/api/v1/prospective/performance');
  expect(Object.fromEntries(url.searchParams)).toEqual({view:'research',competition:'ALL',market:'ALL',cohort:'ALL',as_of:f.asOf});
  expect(fetcher.mock.calls[0][1]).toMatchObject({cache:'no-store'});
 });
 it.each(['selection','membership','count','probability','future','primary','market','score'])( 'rejects inconsistent %s without fallback',kind=>{
  const value=structuredClone(fixture);
  if(kind==='selection')value.selection.cohort='BOOKMAKER_RESEARCH';
  if(kind==='membership')value.historical_store_membership='VERIFIED';
  if(kind==='count')value.unique_fixtures=2;
  if(kind==='probability')value.forecasts[0].probabilities[0].probability=2;
  if(kind==='future')value.forecasts[0].frozen_at_utc='2999-01-01T00:00:00Z';
  if(kind==='primary')value.forecasts[0].selected_for_metrics=false;
  if(kind==='market')value.markets.reverse();
  if(kind==='score')value.markets[1].probability_scores.sample_count=2;
  expect(()=>decodeResearchPerformance(value,filters)).toThrow();
 });
 it('rejects stale selection even on an empty report',()=>{
  const value={...fixture,forecasts:[],forecast_cohorts:0,unique_fixtures:0};
  expect(()=>decodeResearchPerformance(value,{...filters,competition:'E0'})).toThrow();
 });
});
