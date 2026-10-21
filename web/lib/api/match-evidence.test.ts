import { expect, it, vi } from 'vitest';
import raw from '../../../tests/fixtures/public_match_evidence.json';
import picks from '../../../tests/fixtures/strategy_picks.json';
import { decodeStrategyPage } from './track-record';
import { decodeMatchEvidence } from './match-evidence';
import { createApiClient } from './client';
const pick = decodeStrategyPage(picks).publications[0];
it('accepts the backend generated projection and rejects extra private fields', () => {
  expect(decodeMatchEvidence(raw,pick).state).toBe('PARTIAL');
  for (const path of [[],['fixture'],['publication'],['publication','offer'],['team_corners'],['saved_btts']]) {
    const value = structuredClone(raw) as any;
    let target = value; for (const key of path) target = target[key];
    target.private_path = '/private/secret';
    expect(()=>decodeMatchEvidence(value,pick)).toThrow();
  }
});
it('rejects changed exact teams, kickoff and frozen publication values',()=>{
  for (const key of ['home_team','away_team','kickoff_utc','competition'] as const) {
    expect(()=>decodeMatchEvidence({...raw,fixture:{...raw.fixture,[key]:'other'}},pick)).toThrow();
  }
  expect(()=>decodeMatchEvidence({...raw,publication:{...raw.publication,offer:{...raw.publication.offer,american_odds:999}}},pick)).toThrow();
});
it('requests only the public digest route, without cache or fixture paths',async()=>{
  const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(raw)));
  const client=createApiClient('live','http://local/api/v1');
  await client.matchEvidence(pick);
  expect(fetcher).toHaveBeenCalledWith(`http://local/api/v1/publications/${pick.publication_id}/evidence`,expect.objectContaining({cache:'no-store'}));
  await expect(client.matchEvidence({...pick,publication_id:'../../private'})).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1); fetcher.mockRestore();
});

it('rejects inconsistent forecast times, probabilities, sides and sample denominators', () => {
  const mutations: ((v: typeof raw) => void)[] = [
    v => { v.publication.forecast_created_at_utc = '2099-01-01T00:00:00Z'; },
    v => { v.saved_btts.comparisons[0].yes.model_probability = 42; },
    v => { v.saved_btts.comparisons[0].yes.side = 'NO'; },
    v => { v.saved_btts.comparisons[0].yes.decimal_odds = 0; },
    v => { v.saved_btts.omitted = -1; },
    v => { v.saved_btts.comparisons[0].current_status = 'LIVE'; },
    v => { v.team_corners.teams[0].season.n = 1; v.team_corners.teams[0].season.completed = 0; },
    v => { v.team_corners.teams[0].missing_corner_rows = -1; },
    v => { v.team_corners.teams[0].small_sample = false; },
    v => { v.team_corners.teams[1] = structuredClone(v.team_corners.teams[0]); },
  ];
  for (const mutate of mutations) {
    const malformed = structuredClone(raw); mutate(malformed);
    expect(()=>decodeMatchEvidence(malformed,pick)).toThrow('MATCH_EVIDENCE_CONTRACT_MISMATCH');
  }
});
it('keeps generic route errors distinct from explicit saved-publication absence', async () => {
  const fetcher = vi.spyOn(globalThis,'fetch');
  const client = createApiClient('live','http://local/api/v1');
  fetcher.mockResolvedValueOnce(new Response('Not Found',{status:404}));
  await expect(client.matchEvidence(pick)).rejects.toMatchObject({status:404,code:'NETWORK_ERROR'});
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({error:{code:'PUBLICATION_EVIDENCE_NOT_FOUND',message:'No saved publication evidence was found.',retryable:false}}),{status:404}));
  await expect(client.matchEvidence(pick)).rejects.toMatchObject({status:404,code:'PUBLICATION_EVIDENCE_NOT_FOUND'});
  fetcher.mockRestore();
});
