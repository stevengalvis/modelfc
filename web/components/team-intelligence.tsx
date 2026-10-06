"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";
import type {
  Insight,
  Sample,
  TeamList,
  TeamProfile,
  TeamSummary,
  TeamOverview,
} from "@/lib/api/team-intelligence";

const TABLE_COLUMNS = [
  ["team", "Team"],
  ["matches", "Matches"],
  ["won", "Won / match"],
  ["conceded", "Conceded / match"],
  ["differential", "Differential"],
  ["total", "Match total"],
] as const;
const METRIC_KEYS = ["won", "conceded", "differential", "total"] as const;
const MOBILE_METRICS = [
  ["won", "Won"],
  ["conceded", "Conceded"],
  ["differential", "Difference"],
  ["total", "Total"],
] as const;
const PRIMARY_METRICS = [
  ["won", "Corners won"],
  ["conceded", "Corners conceded"],
  ["differential", "Differential"],
  ["total", "Match total corners"],
] as const;
const RECENCY_WINDOWS = [
  ["last_five", "Last 5"],
  ["previous_five", "Previous 5"],
  ["last_ten", "Last 10"],
] as const;
type SortState = { key: (typeof TABLE_COLUMNS)[number][0]; desc: boolean };

const value = (n: number | null) => (n === null ? "Unavailable" : n.toFixed(2));
const signed = (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
const season = (s: string) => `20${s.slice(0, 2)}/${s.slice(2)}`;
export function patternFact(item: Insight) {
  switch (item.family) {
    case "THRESHOLD_STREAK":
      return `${item.streak} straight matches with ${item.threshold}+ corners`;
    case "HIGH_MATCH_CORNER_ENVIRONMENT":
    case "LOW_MATCH_CORNER_ENVIRONMENT":
      return `${value(item.recent.total)} total corners per match through ${item.recent.n} matches`;
    case "HOME_AWAY_SPLIT":
      return `${value(item.recent.won)} corners won at home vs ${value(item.baseline!.won)} away (${item.recent.n} / ${item.baseline!.n} matches)`;
    default: {
      const label =
        item.metric === "won"
          ? "Corners won"
          : item.metric === "conceded"
            ? "Corners conceded"
            : "Corner differential";
      return `${label} ${signed(item.difference!)} per match: ${value(item.recent[item.metric])} in last 5 vs ${value(item.baseline![item.metric])} in previous 5`;
    }
  }
}
function Patterns({ items }: { items: Insight[] }) {
  return (
    <section className="panel ti-patterns">
      <div className="section-title">
        <div>
          <h2>Notable patterns</h2>
          <p>Observed corner patterns with explicit samples</p>
        </div>
      </div>
      {items.length ? (
        <div className="ti-pattern-grid">
          {items.map((item) => (
            <article key={`${item.team.team_id}-${item.family}`}>
              <Link href={`/teams/${item.team.team_id}`}>
                {item.team.display_name}
              </Link>
              <p>{patternFact(item)}</p>
              <small>
                {item.recent.start_date} to {item.recent.end_date}
              </small>
            </article>
          ))}
        </div>
      ) : (
        <p className="ledger-message">
          No patterns meet the current sample and threshold rules.
        </p>
      )}
    </section>
  );
}
function Trend({ team }: { team: TeamSummary | TeamOverview }) {
  return (
    <span className="ti-trend">
      {team.recency.trend_state === "AVAILABLE"
        ? `${signed(team.recency.won_change!)} won / match vs previous 5`
        : team.recency.trend_state === "INCOMPLETE_COVERAGE"
          ? "Trend unavailable: missing corners"
          : "Trend available after 10 matches"}
    </span>
  );
}
export function useRead<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setLoading(true);
    setError(null);
    load(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error ? reason.message : "Request failed.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [load, attempt]);
  return { data, loading, error, retry: () => setAttempt((n) => n + 1) };
}
export function ReadState({
  loading,
  error,
  retry,
}: {
  loading: boolean;
  error: string | null;
  retry: () => void;
}) {
  if (loading)
    return (
      <section className="panel ledger-message" role="status">
        Loading team intelligence…
      </section>
    );
  if (error)
    return (
      <section className="error-banner" role="alert">
        <strong>Team intelligence unavailable.</strong>
        <span>{error}</span>
        <button className="secondary-button" onClick={retry}>
          Retry
        </button>
      </section>
    );
  return null;
}
export function DataDate({ metadata }: { metadata: TeamList["metadata"] }) {
  return (
    <p className="ti-data-date">
      EFL Championship · {season(metadata.season)} · Data through{" "}
      {metadata.data_cutoff ?? "no completed results"}
      {metadata.roster_state === "PARTIAL" ? " · Partial roster coverage" : ""}
    </p>
  );
}
function TeamsTable({
  teams,
  sort,
  onSort,
}: {
  teams: TeamOverview[];
  sort: SortState;
  onSort: (sort: SortState) => void;
}) {
  return (
    <div className="ti-desktop">
      <table className="ti-table">
        <thead>
          <tr>
            {TABLE_COLUMNS.map(([key, label]) => (
              <th
                key={key}
                aria-sort={
                  sort.key === key
                    ? sort.desc
                      ? "descending"
                      : "ascending"
                    : "none"
                }
              >
                <button
                  onClick={() =>
                    onSort({
                      key,
                      desc:
                        sort.key === key
                          ? !sort.desc
                          : key !== "team" && key !== "conceded",
                    })
                  }
                >
                  {label}
                  {sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
                </button>
              </th>
            ))}
            <th>Recent corners won</th>
          </tr>
        </thead>
        <tbody>
          {teams.map((team) => (
            <tr key={team.team.team_id}>
              <td>
                <Link href={`/teams/${team.team.team_id}`}>
                  {team.team.display_name}
                </Link>
              </td>
              <td>
                {team.coverage.completed}
                {team.coverage.missing > 0 && (
                  <small>{team.coverage.covered} covered</small>
                )}
              </td>
              {METRIC_KEYS.map((metric) => (
                <td className="mono" key={metric}>
                  {value(team.primary[metric])}
                </td>
              ))}
              <td>
                <Trend team={team} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function MobileTeamRows({ teams }: { teams: TeamOverview[] }) {
  return (
    <div className="ti-mobile">
      {teams.map((team) => (
        <article className="ti-team-row" key={team.team.team_id}>
          <div>
            <Link href={`/teams/${team.team.team_id}`}>
              {team.team.display_name}
            </Link>
            <small>
              {team.coverage.completed} matches · {team.coverage.covered}{" "}
              covered
            </small>
          </div>
          <dl>
            {MOBILE_METRICS.map(([metric, label]) => (
              <div key={metric}>
                <dt>{label} / match</dt>
                <dd>{value(team.primary[metric])}</dd>
              </div>
            ))}
          </dl>
          <Trend team={team} />
        </article>
      ))}
    </div>
  );
}
function PrimaryMetrics({ team }: { team: TeamSummary }) {
  return (
    <section className="ti-primary">
      {PRIMARY_METRICS.map(([metric, label]) => (
        <article className="panel" key={metric}>
          <h2>{label} / match</h2>
          <strong>{value(team.primary[metric])}</strong>
          <p>
            {team.ranks[metric].rank === null
              ? "Rank available after 5 covered matches"
              : `Rank ${team.ranks[metric].rank} of ${team.ranks[metric].cohort_size} eligible teams`}
          </p>
          <small>
            {team.primary.n} matches
            {metric === "conceded" ? " · Lowest rate ranks first" : ""}
          </small>
        </article>
      ))}
    </section>
  );
}
function RecentForm({ team }: { team: TeamSummary }) {
  return (
    <section className="panel">
      <div className="section-title">
        <div>
          <h2>Recent form</h2>
          <p>Exact, non-overlapping completed-match windows</p>
        </div>
      </div>
      <div className="ti-split">
        {RECENCY_WINDOWS.map(([key, label]) => {
          const w = team.recency[key];
          return w.state === "AVAILABLE" ? (
            <SampleMetrics key={key} sample={w.sample} label={label} />
          ) : (
            <div key={key}>
              <h3>{label}</h3>
              <p>
                {w.state === "INSUFFICIENT_SAMPLE"
                  ? "Not enough completed matches"
                  : "Unavailable: missing corner data"}
              </p>
            </div>
          );
        })}
      </div>
      <p className="ti-footnote">
        <Trend team={team} />
      </p>
    </section>
  );
}
function MatchThresholds({ team }: { team: TeamSummary }) {
  return (
    <section className="panel">
      <div className="section-title">
        <div>
          <h2>Match corner thresholds</h2>
          <p>Observed frequency across {team.primary.n} covered matches</p>
        </div>
      </div>
      <div className="ti-thresholds">
        {team.thresholds.map((t) => (
          <div key={t.threshold}>
            <strong>{t.threshold}+ corners</strong>
            <span>
              {t.count} / {t.denominator} ·{" "}
              {t.frequency === null
                ? "Unavailable"
                : `${(t.frequency * 100).toFixed(1)}%`}
            </span>
            <div className="ti-frequency" aria-hidden="true">
              <i style={{ width: `${(t.frequency ?? 0) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
function RecentMatches({
  matches,
}: {
  matches: TeamProfile["recent_matches"];
}) {
  const cornerScale = Math.max(
    1,
    ...matches.flatMap((m) => [m.won ?? 0, m.conceded ?? 0]),
  );
  return (
    <section className="panel">
      <div className="section-title">
        <div>
          <h2>Recent matches</h2>
          <p>Newest first · Corners won in cyan, conceded in violet</p>
        </div>
      </div>
      <div className="ti-recent">
        {matches.map((m) => (
          <article key={`${m.date}-${m.opponent.team_id}`}>
            <div>
              <strong>{m.opponent.display_name}</strong>
              <small>
                {m.date} · {m.venue === "HOME" ? "Home" : "Away"}
              </small>
            </div>
            <div className="ti-match-bars" aria-hidden="true">
              {[m.won, m.conceded].map((n, i) => (
                <i
                  key={i}
                  className={i ? "conceded" : "won"}
                  style={{ width: `${((n ?? 0) / cornerScale) * 100}%` }}
                />
              ))}
            </div>
            <span>
              {m.won === null
                ? "Corners unavailable"
                : `${m.won} won · ${m.conceded} conceded · ${m.total} total`}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

export function TeamsDashboard() {
  const load = useCallback((signal: AbortSignal) => api.teams(signal), []);
  const state = useRead(load);
  const [sort, setSort] = useState<SortState>({ key: "team", desc: false });
  const ordered = useMemo(
    () =>
      [...(state.data?.teams ?? [])].sort((a, b) => {
        const x =
          sort.key === "team"
            ? a.team.team_id
            : sort.key === "matches"
              ? a.coverage.completed
              : a.primary[sort.key];
        const y =
          sort.key === "team"
            ? b.team.team_id
            : sort.key === "matches"
              ? b.coverage.completed
              : b.primary[sort.key];
        if (x === null || y === null)
          return x === y
            ? a.team.team_id.localeCompare(b.team.team_id, "en")
            : x === null
              ? 1
              : -1;
        const delta =
          typeof x === "string"
            ? x.localeCompare(y as string, "en")
            : x - (y as number);
        return (
          (sort.desc ? -delta : delta) ||
          a.team.team_id.localeCompare(b.team.team_id, "en")
        );
      }),
    [state.data, sort],
  );

  return (
    <div className="workspace ti-workspace">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Championship corners</p>
          <h1>Team Intelligence</h1>
          <Link className="text-button" href="/teams/compare">Compare two teams →</Link>
          {state.data ? (
            <DataDate metadata={state.data.metadata} />
          ) : (
            <p>Season rates, venue splits, and recent patterns.</p>
          )}
        </div>
      </header>
      <ReadState {...state} />
      {state.data &&
        (ordered.length ? (
          <>
            <section className="panel">
              <div className="section-title">
                <div>
                  <h2>Across the Championship</h2>
                  <p>
                    {ordered.length} teams · Descriptive results, not forecasts
                  </p>
                </div>
              </div>
              <TeamsTable teams={ordered} sort={sort} onSort={setSort} />
              <MobileTeamRows teams={ordered} />
            </section>
            <Patterns items={state.data.insights} />
            <p className="ti-footnote">
              Source: Football-Data. Averages use complete corner pairs. Ranks
              require five covered matches.
            </p>
          </>
        ) : (
          <section className="panel ledger-message">
            No completed current-season fixtures are available.
          </section>
        ))}
    </div>
  );
}
function SampleMetrics({ sample, label }: { sample: Sample; label: string }) {
  return (
    <div>
      <h3>{label}</h3>
      <p>
        {sample.n} of {sample.completed} matches covered
      </p>
      <dl className="ti-compact-metrics">
        <div>
          <dt>Won / match</dt>
          <dd>{value(sample.won)}</dd>
        </div>
        <div>
          <dt>Conceded / match</dt>
          <dd>{value(sample.conceded)}</dd>
        </div>
      </dl>
    </div>
  );
}
export function TeamProfileDashboard({ teamId }: { teamId: string }) {
  const load = useCallback(
    (signal: AbortSignal) => api.teamProfile(teamId, signal),
    [teamId],
  );
  const state = useRead<TeamProfile>(load);
  const team = state.data?.summary;

  return (
    <div className="workspace ti-workspace">
      <Link className="text-button" href="/teams">
        ← All teams
      </Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">Team profile</p>
          <h1>{team?.team.display_name ?? "Team Intelligence"}</h1>
          {state.data && (
            <>
              <DataDate metadata={state.data.metadata} />
              <p>
                {team!.coverage.completed} completed matches ·{" "}
                {team!.coverage.covered} with corners · {team!.coverage.missing}{" "}
                missing
              </p>
            </>
          )}
        </div>
      </header>
      <ReadState {...state} />
      {state.data && team && (
        <>
          <Link className="text-button" href={`/teams/compare?team=${team.team.team_id}`}>Compare with another team →</Link>
          <PrimaryMetrics team={team} />
          <section className="panel">
            <div className="section-title">
              <div>
                <h2>Home and away</h2>
                <p>Current-season venue samples</p>
              </div>
            </div>
            <div className="ti-split">
              <SampleMetrics sample={team.home} label="Home" />
              <SampleMetrics sample={team.away} label="Away" />
            </div>
          </section>
          <RecentForm team={team} />
          <MatchThresholds team={team} />
          <RecentMatches matches={state.data.recent_matches} />
          <Patterns items={state.data.insights} />
          <p className="ti-footnote">
            Source: Football-Data · Current season only · Missing corners remain
            unavailable. Patterns use fixed sample and threshold rules, not
            significance tests.
          </p>
        </>
      )}
    </div>
  );
}
