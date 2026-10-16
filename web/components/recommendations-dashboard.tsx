"use client";

import Link from "next/link";
import { StrategyPicks } from "./strategy-picks";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/client";
import type { RecordedOffer, RecordedOfferStatus, ShortlistOffer, ShortlistRanking } from "@/lib/api/types";

const percent = (value: number | null) => value === null ? "Not available" : `${(value * 100).toFixed(1)}%`;
const signed = (value: number, digits = 0) => `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
const utcFormatter = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "medium", timeZone: "UTC",
});
const utc = (value: string) => utcFormatter.format(new Date(value)) + " UTC";
const age = (seconds: number) => seconds < 60 ? `${Math.floor(seconds)}s` : seconds < 3600
  ? `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s` : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
const labels: Record<RecordedOfferStatus, string> = {
  RECENT_QUALIFYING: "Recently checked · qualifying", PRICE_NEEDS_RECHECKING: "Price needs rechecking",
  QUOTE_UNAVAILABLE: "Quote unavailable at last check", NO_QUALIFYING_EDGE: "No qualifying edge",
  VALUE_UNVERIFIED: "Value comparison incomplete", REVIEW_REQUIRED: "Price needs review", HISTORICAL: "Historical quote · kickoff passed",
};

function displayStatus(item: RecordedOffer, asOf: number, elapsed: number, failed: boolean): RecordedOfferStatus {
  if (item.status === "HISTORICAL" || asOf + elapsed * 1000 >= Date.parse(item.kickoff_utc)) return "HISTORICAL";
  if (["QUOTE_UNAVAILABLE", "REVIEW_REQUIRED"].includes(item.status)) return item.status;
  if (item.status === "RECENT_QUALIFYING" && (failed || item.observation_age_seconds + elapsed > 300)) return "PRICE_NEEDS_RECHECKING";
  return item.status;
}

function Offer({ item, asOf, elapsed, failed }: { item: RecordedOffer; asOf: number; elapsed: number; failed: boolean }) {
  const status = displayStatus(item, asOf, elapsed, failed);
  return <div className="recorded-offer">
    <div className="recorded-offer-heading"><h3>{item.team} {item.direction.toLowerCase()} {item.line} corners</h3>
      <span className={`recorded-status ${status === "RECENT_QUALIFYING" ? "recent" : ""}`}>{labels[status]}</span></div>
    <div className="recorded-price"><strong>{item.bookmaker} · {signed(item.american_odds)}</strong>
      <span>Recorded price · {item.decimal_odds.toFixed(2)} decimal</span></div>
    <dl className="recorded-metrics">
      <div><dt>Modeled win probability</dt><dd>{percent(item.model_probability)}</dd></div>
      <div><dt>No-vig edge at quote</dt><dd>{item.no_vig_probability_edge === null ? "Not available" : `${signed(item.no_vig_probability_edge * 100, 1)} pp`}</dd></div>
      <div><dt>Expected value at quote</dt><dd>{item.expected_profit === null ? "Not available" : `${signed(item.expected_profit * 100, 1)}% per unit risked`}</dd></div>
    </dl>
    <p className="ledger-meta">Observed <time dateTime={item.retrieved_at_utc}>{utc(item.retrieved_at_utc)}</time> · Age {age(item.observation_age_seconds + elapsed)}</p>
    {item.availability_checked_at_utc && <p className="ledger-meta">Last availability check <time dateTime={item.availability_checked_at_utc}>{utc(item.availability_checked_at_utc)}</time></p>}
    {item.opportunity_id ? <Link href={`/opportunities/${item.opportunity_id}`}>Recorded opportunity evidence</Link>
      : <Link href={`/predictions#prediction-${item.prediction_id}`}>Recorded forecast evidence</Link>}
  </div>;
}


type Clock = { mono: number; wall: number };
const clock = (): Clock => ({ mono: performance.now(), wall: Date.now() });
const elapsedSince = (tick: Clock, started: Clock) => Math.max(0, tick.mono - started.mono, tick.wall - started.wall) / 1000;

// Retain the successful snapshot during refresh, failure and a stalled request.
// Ages advance conservatively even across suspended tabs or wall-clock corrections.
function useSavedRead<T>(load: (signal: AbortSignal) => Promise<T>, pauseOnFailure = false) {
  const [snapshot, setSnapshot] = useState<{ data: T; started: Clock; elapsed: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const advance = () => setSnapshot(old => old && ({ ...old, elapsed: Math.max(old.elapsed, elapsedSince(clock(), old.started)) }));
    const timer = window.setInterval(advance, 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController(), started = clock();
    let pending = true, requestFailed = false;
    setLoading(true);
    const refresh = () => {
      if (pauseOnFailure && requestFailed) return;
      setSnapshot(old => old && ({ ...old, elapsed: Math.max(old.elapsed, elapsedSince(clock(), old.started)) }));
      setAttempt(v => v + 1);
    };
    const timer = window.setTimeout(() => {
      if (pending) {
        setFailed(true);
        if (pauseOnFailure) {
          requestFailed = true;
          controller.abort();
          setLoading(false);
          return;
        }
      }
      refresh();
    }, 60_000);
    const resume = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", resume);
    load(controller.signal).then(data => {
      if (!controller.signal.aborted) { setSnapshot({ data, started, elapsed: elapsedSince(clock(), started) }); setFailed(false); }
    }).catch(() => { if (!controller.signal.aborted) { requestFailed = true; setFailed(true); } }).finally(() => {
      pending = false;
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => { controller.abort(); window.clearTimeout(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", resume); };
  }, [load, attempt, pauseOnFailure]);
  return { data: snapshot?.data, elapsed: snapshot?.elapsed ?? 0, failed, loading, retry: () => setAttempt(v => v + 1) };
}

function Alternatives({ item }: { item: ShortlistOffer }) {
  const load = useCallback((signal: AbortSignal) => api.alternatives(item.fixture_id, signal), [item.fixture_id]);
  const { data, elapsed, failed, retry } = useSavedRead(load);
  const fixture = data?.fixtures.find(f => f.fixture_id === item.fixture_id && f.selected_prediction_id === item.prediction_id);
  return <section className="panel" aria-label="Alternative bookmakers and lines">
    <h2>{item.home_team} vs {item.away_team} · alternatives</h2>
    <p>Recorded prices from the latest frozen capture. Each line retains its own qualification status.</p>
    {failed && <p role="alert">Alternatives could not be refreshed. Previous prices need rechecking. <button onClick={retry}>Retry alternatives</button></p>}
    {!data && !failed && <p role="status">Loading alternatives…</p>}
    {data && (!fixture || Date.parse(fixture.kickoff_utc) <= Date.parse(data.as_of_utc) + elapsed * 1000)
      ? <p>These alternatives are no longer available. Refresh the shortlist.</p>
      : fixture?.offers.map(o => <Offer key={`${o.target_id}:${o.bookmaker}`} item={o} asOf={Date.parse(data!.as_of_utc)} elapsed={elapsed} failed={failed} />)}
  </section>;
}

export function SavedPriceAnalysis() {
  const [ranking, setRanking] = useState<ShortlistRanking>("higher_probability");
  // A new mode starts at five and aborts the old read instead of showing its snapshot.
  return <div className="workspace recommendations-workspace">
    <header className="page-heading"><div><span className="eyebrow">Saved qualifying prices</span><h2>Saved-price analysis</h2>
      <p>Ranked upcoming choices at recorded prices. Recheck the price and availability before acting.</p></div></header>
    <label>Ranking <select value={ranking} onChange={event => setRanking(event.target.value as ShortlistRanking)}>
      <option value="higher_probability">Higher probability (65%+)</option>
      <option value="best_value">Best value</option>
    </select></label>
    <p className="recorded-note">{ranking === "higher_probability"
      ? "At least 65% modeled win probability, ranked by win probability then value. All existing value checks still apply."
      : "Ranked by the existing value criteria. No additional win-probability minimum."}
      {" "}Pushes do not count as wins. Model estimates are not proven hit rates.</p>
    <RankedRecommendations key={ranking} ranking={ranking} />
  </div>;
}

function RankedRecommendations({ ranking }: { ranking: ShortlistRanking }) {
  const [limit, setLimit] = useState(5);
  const [expanded, setExpanded] = useState<string | null>(null);
  const load = useCallback((signal: AbortSignal) => api.shortlist(limit, signal, ranking), [limit, ranking]);
  const { data, elapsed, failed, loading, retry } = useSavedRead(load, limit > 5);
  const sentinel = useRef<HTMLDivElement>(null);
  const scrolled = useRef(false);
  const requesting = useRef(false);
  const hasMore = !!data && data.total > data.limit && data.limit < 100;
  useEffect(() => {
    requesting.current = false;
    scrolled.current = false;
  }, [data, failed]);
  const loadMore = useCallback(() => {
    if (!data || !hasMore || failed || loading || requesting.current || limit !== data.limit) return;
    requesting.current = true;
    scrolled.current = false;
    setLimit(Math.min(100, data.limit + 5));
  }, [data, hasMore, failed, loading, limit]);
  useEffect(() => {
    if (!hasMore || !sentinel.current || typeof IntersectionObserver === "undefined") return;
    let nearBottom = false, active = true;
    const onScroll = () => {
      scrolled.current = true;
      if (nearBottom) loadMore();
    };
    // A visible sentinel alone must not drain every batch on mount or after a read.
    const observer = new IntersectionObserver(entries => {
      if (!active) return;
      nearBottom = entries.some(entry => entry.isIntersecting);
      if (nearBottom && scrolled.current) loadMore();
    }, { rootMargin: "200px 0px" });
    observer.observe(sentinel.current);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { active = false; observer.disconnect(); window.removeEventListener("scroll", onScroll); };
  }, [hasMore, loadMore]);
  const asOf = data ? Date.parse(data.as_of_utc) : 0;
  const offers = data?.offers.filter(o => Date.parse(o.kickoff_utc) > asOf + elapsed * 1000) ?? [];
  const selected = offers.find(o => o.fixture_id === expanded);
  return <>
    <p className="recorded-note">Saved data is checked every minute. This page does not request new sportsbook odds. Older qualifying quotes stay visible with a price-recheck label.</p>
    <p className="recorded-note">Analysis choices and alternative lines; these are not the published pick record. <Link href="/performance">View published recommendations and results</Link></p>
    <p className="recorded-note">Latest capture per fixture, best qualifying bookmaker per target. Ranking covers up to 100 nearest upcoming fixtures. <Link href="/predictions">Forecast ledger and past fixtures</Link></p>
    {failed && <section className="panel ledger-message" role="alert"><h2>Shortlist could not be refreshed</h2>
      <p>{data ? "Showing the previous read. Prices need rechecking." : "Saved choices are unavailable."}</p><button onClick={retry}>Retry</button></section>}
    {!data && !failed && <p role="status">Loading qualifying choices…</p>}
    {data && <><p className="ledger-meta">Saved data read <time dateTime={data.as_of_utc}>{utc(data.as_of_utc)}</time></p>
      {!offers.length && <p>{ranking === "higher_probability"
        ? "No upcoming choices meet both the value checks and at least 65% modeled win probability at saved prices."
        : "No qualifying upcoming choices at saved prices."}</p>}
      <div className="recorded-fixtures">{offers.map((item, index) => <article className="panel" key={`${item.fixture_id}:${item.target_id}`}>
        <p className="ledger-meta">#{index + 1} · {item.competition} · Kickoff {utc(item.kickoff_utc)}</p>
        <h2>{item.home_team} vs {item.away_team}</h2>
        <Offer item={item} asOf={asOf} elapsed={elapsed} failed={failed} />
        <button className="secondary-button" onClick={() => setExpanded(v => v === item.fixture_id ? null : item.fixture_id)}
          aria-expanded={expanded === item.fixture_id}>Bookmakers and alternate lines</button>
      </article>)}</div>
      {hasMore && <div ref={sentinel} aria-label="More qualifying choices">
        {loading && <p role="status">Loading more qualifying choices…</p>}
        <button className="secondary-button" disabled={loading} onClick={failed ? retry : loadMore}>
          {failed ? "Retry loading qualifying choices" : "Show more qualifying choices"}
        </button>
      </div>}
      {!hasMore && offers.length > 0 && data.total <= 100 && <p role="status">All qualifying choices loaded.</p>}
      {data.total > 100 && data.limit === 100 && <p>Showing the top 100 qualifying choices. Full evidence remains in the forecast ledger.</p>}
      {selected && <><button onClick={() => setExpanded(null)}>Close alternatives</button><Alternatives key={selected.prediction_id} item={selected} /></>}
    </>}
  </>;
}

export function RecommendationsDashboard() {
  const [showAnalysis,setShowAnalysis]=useState(false);
  return <div className="workspace recommendations-workspace">
    <header className="page-heading"><h1>Recommendations</h1><p>Published current choices at saved prices.</p></header>
    <StrategyPicks scope="current" />
    <button onClick={()=>setShowAnalysis(v=>!v)}>{showAnalysis ? "Hide" : "Show"} alternative-price analysis</button>
    {showAnalysis && <SavedPriceAnalysis />}
  </div>;
}
