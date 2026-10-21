"use client";
import { useEffect, useState } from 'react';
import { ModelFCApiError } from '@/lib/api/errors';
import { api } from '@/lib/api/client';
import type { StrategyPick } from '@/lib/api/track-record';
import type { MatchEvidence } from '@/lib/api/match-evidence';
const label = (state: string) => state.toLowerCase().replaceAll('_', ' ');
export function MatchEvidenceDisclosure({ pick, open }: { pick: StrategyPick; open: boolean }) {
  const [data, setData] = useState<MatchEvidence | null>(null);
  const [error, setError] = useState(false);
  const [missing, setMissing] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const supported = pick.offer.competition === 'E1' && ['TEAM_TOTAL','BTTS'].includes(pick.offer.market_type);
  useEffect(() => {
    if (!open || !supported) return;
    const controller = new AbortController();
    setData(null); setError(false); setMissing(false);
    api.matchEvidence(pick, controller.signal).then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(cause => { if (!controller.signal.aborted) { if (cause instanceof ModelFCApiError && cause.status === 404) setMissing(true); else setError(true); } });
    return () => controller.abort();
  }, [open, supported, pick, attempt]);
  if (!open) return null;
  if (!supported || data?.state === 'UNSUPPORTED') return <p>Evidence summary unsupported: Championship corners and BTTS only.</p>;
  if (missing) return <p>No saved publication evidence was found.</p>;
  if (error) return <p role="alert">Saved evidence could not be loaded; no missing-data conclusion is inferred. <button onClick={() => setAttempt(a=>a+1)}>Retry evidence</button></p>;
  if (!data) return <p role="status">Loading saved evidence…</p>;
  return <section aria-label="Saved match evidence">
    <h4>Saved match evidence · partial</h4>
    <p>Read {data.as_of_utc}. Separate saved sources, not an atomic live snapshot or a complete fixture calendar.</p>
    <p>Frozen publication forecast: {(Number(data.publication.offer.model_probability)*100).toFixed(1)}% · saved {data.publication.forecast_created_at_utc}. Quote: {label(data.publication.quote_state)}. Model version and history cutoff are not included in this publication projection. Publication evidence remains unchanged by newer observations.</p>
    <p>Corner research snapshot: not bound to this publication.</p>
    <p>Descriptive team corners: {label(data.team_corners.state)}. Acquisition freshness unknown. {data.team_corners.cutoff_date && `Results through ${data.team_corners.cutoff_date}; separate from frozen forecast inputs.`}</p>
    {data.team_corners.teams.map(t => <div key={t.venue}>
      <p><strong>{t.team} · {label(t.venue)}</strong> · {label(t.state)}{t.small_sample && ' · small venue sample'}</p>
      {([{name:'Season',sample:t.season},{name:'Venue',sample:t.venue_sample}]).map(({name,sample:s}) => {
        return <p key={String(name)}>{String(name)}: {s.won === null ? 'unavailable' : `${s.won.toFixed(2)} won / ${s.conceded?.toFixed(2)} conceded per match`} · {s.n}/{s.completed} completed matches with corner data.</p>;
      })}
      <p>{t.missing_corner_rows} completed matches missing corners.</p>
    </div>)}
    <p>Saved BTTS observations: {label(data.saved_btts.state)} · independent saved inventory at read time; not publication replacements.</p>
    {data.saved_btts.comparisons.map((c,i)=><p key={i}>{c.bookmaker} · {label(c.current_status)} · recorded {c.observation_timestamp_utc}. Yes {c.yes.american_odds > 0 ? '+' : ''}{c.yes.american_odds}, No {c.no.american_odds > 0 ? '+' : ''}{c.no.american_odds}. Saved forecast {c.model_name} {c.model_version}: {(c.yes.model_probability*100).toFixed(1)}% Yes, frozen {c.frozen_at_utc}, history cutoff {c.history_cutoff_date}.</p>)}
    {!!data.saved_btts.omitted && <p>{data.saved_btts.omitted} additional saved comparisons omitted.</p>}
  </section>;
}
