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
