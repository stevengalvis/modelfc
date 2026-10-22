import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '@/lib/api/client';
import { ModelFCApiError } from '@/lib/api/errors';
import { decodeMatchEvidence, type MatchEvidence } from '@/lib/api/match-evidence';
import { decodeStrategyPage } from '@/lib/api/track-record';
import picks from '../../tests/fixtures/strategy_picks.json';
import raw from '../../tests/fixtures/public_match_evidence.json';
import { MatchEvidenceDisclosure } from './match-evidence-disclosure';
const pick=decodeStrategyPage(picks).publications[0];
const evidence=decodeMatchEvidence(raw,pick);
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('loads on demand, aborts on close and displays separate partial stale evidence',async()=>{
  let resolve!: (v:MatchEvidence)=>void;
  const read=vi.spyOn(api,'matchEvidence').mockImplementation(()=>new Promise(r=>{resolve=r;}));
  const view=render(<MatchEvidenceDisclosure pick={pick} open={false}/>);
  expect(read).not.toHaveBeenCalled();
  view.rerender(<MatchEvidenceDisclosure pick={pick} open/>);
  expect(screen.getByRole('status')).toHaveTextContent('Loading');
  await act(async()=>resolve(evidence));
  expect(screen.getByRole('region')).toHaveTextContent('not an atomic live snapshot');
  expect(screen.getByRole('region')).toHaveTextContent('Acquisition freshness unknown');
  expect(screen.getByRole('region')).toHaveTextContent('stale');
  view.rerender(<MatchEvidenceDisclosure pick={pick} open={false}/>);
  expect(read.mock.calls[0][1]?.aborted).toBe(true);
  expect(screen.queryByRole('region')).not.toBeInTheDocument();
});
it('distinguishes error with retry, missing and unsupported',async()=>{
  const read=vi.spyOn(api,'matchEvidence').mockRejectedValueOnce(new Error()).mockRejectedValueOnce(new ModelFCApiError('missing','PUBLICATION_EVIDENCE_NOT_FOUND',false,404));
  const view=render(<MatchEvidenceDisclosure pick={pick} open/>);
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button',{name:'Retry evidence'}));
  await screen.findByText('No saved publication evidence was found.');
  view.rerender(<MatchEvidenceDisclosure pick={{...pick,offer:{...pick.offer,competition:'E0'}} as typeof pick} open/>);
  expect(screen.getByText(/summary unsupported/)).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(2);
});

it('does not infer missing evidence from an unavailable ingress route', async () => {
  vi.spyOn(api,'matchEvidence').mockRejectedValue(new ModelFCApiError('not found','NETWORK_ERROR',false,404));
  render(<MatchEvidenceDisclosure pick={pick} open/>);
  await screen.findByRole('alert');
  expect(screen.queryByText('No saved publication evidence was found.')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Retry evidence'})).toBeInTheDocument();
});
