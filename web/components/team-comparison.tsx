"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";
import type { Sample, TeamProfile, TeamSummary, Window } from "@/lib/api/team-intelligence";
import { DataDate, ReadState, useRead } from "./team-intelligence";

const METRICS = [
  ["won", "Won / match"], ["conceded", "Conceded / match"],
  ["differential", "Differential / match"], ["total", "Total / match"],
] as const;
const WINDOWS = [["last_five", "Last 5"], ["previous_five", "Previous 5"], ["last_ten", "Last 10"]] as const;

function SampleCell({ sample, window }: { sample: Sample; window?: Window }) {
  const available = !window || window.state === "AVAILABLE";
  return <>
    <p>{sample.n} of {sample.completed} matches covered{window ? ` · ${window.requested} required` : ""}</p>
    <p>{sample.start_date ? `${sample.start_date} to ${sample.end_date}` : "No completed matches"}</p>
    {available ? <dl className="ti-compact-metrics">
      {METRICS.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{sample[key]?.toFixed(2) ?? "Unavailable"}</dd></div>)}
    </dl> : <p>{window.state === "INSUFFICIENT_SAMPLE" ? "Not enough completed matches" : "Unavailable: missing corner data"}</p>}
  </>;
}

function ComparisonSection({ title, profiles, sample, window }: {
  title: string; profiles: TeamProfile[];
  sample: (team: TeamSummary) => Sample;
  window?: (team: TeamSummary) => Window;
}) {
  return <section className="panel ti-comparison-section">
    <h2>{title}</h2>
    <div className="ti-comparison-grid">
      {profiles.map(({ summary }) => <article key={summary.team.team_id}>
        <h3>{summary.team.display_name}</h3>
        <SampleCell sample={sample(summary)} window={window?.(summary)} />
      </article>)}
    </div>
  </section>;
}

function ComparisonResults({ left, right }: { left: string; right: string }) {
  const load = useCallback(async (signal: AbortSignal) => {
    const profiles = await Promise.all([api.teamProfile(left, signal), api.teamProfile(right, signal)]);
    const first = profiles[0].metadata;
    // HTTP caches and a refresh between reads can return different populations.
    // Never combine them, even when the displayed cutoff happens to match.
    if (Object.keys(first).some(key => first[key as keyof typeof first] !== profiles[1].metadata[key as keyof typeof first])) {
      throw new Error("The source changed between team reads. Retry to load both teams from the same revision.");
    }
    return profiles;
  }, [left, right]);
  const state = useRead(load);
  const profiles = state.data;
  return <>
    <ReadState {...state} />
    {profiles && <>
      <DataDate metadata={profiles[0].metadata} />
      <section className="panel ti-comparison-grid ti-comparison-summary" aria-label="Compared teams">
        {profiles.map(({ summary }) => <article key={summary.team.team_id}>
          <h2><Link href={`/teams/${summary.team.team_id}`}>{summary.team.display_name} profile →</Link></h2>
          <p>Latest result: {summary.coverage.latest_result_date}</p>
          <p>Latest corners: {summary.coverage.latest_corner_date ?? "Unavailable"}</p>
          <p>{summary.coverage.missing} matches missing corners</p>
        </article>)}
      </section>
      {(["primary", "home", "away"] as const).map((key, i) => <ComparisonSection key={key}
        title={["Season", "Home", "Away"][i]} profiles={profiles} sample={team => team[key]} />)}
      {WINDOWS.map(([key, title]) => <ComparisonSection key={key} title={title} profiles={profiles}
        sample={team => team.recency[key].sample} window={team => team.recency[key]} />)}
      <p className="ti-footnote">Source: Football-Data · Both profiles use the same source revision. Averages use covered corner pairs; schedules and window dates may differ. Recent windows count completed fixtures, including missing corners. Descriptive results, not a matchup forecast.</p>
    </>}
  </>;
}

export function TeamComparison({ initialTeam = "" }: { initialTeam?: string }) {
  const load = useCallback((signal: AbortSignal) => api.teams(signal), []);
  const state = useRead(load);
  const [left, setLeft] = useState(initialTeam);
  const [right, setRight] = useState("");
  const teams = state.data?.teams ?? [];
  const invalid = [left, right].some(id => id && !teams.some(team => team.team.team_id === id));
  const same = !!left && left === right;
  return <div className="workspace ti-workspace">
    <Link className="text-button" href="/teams">← All teams</Link>
    <header className="page-heading"><div><p className="eyebrow">Championship corners</p><h1>Compare teams</h1>
      <p>Season, venue and recent corner samples side by side.</p></div></header>
    <ReadState {...state} />
    {state.data && (teams.length ? <>
      <section className="panel ti-comparison-grid ti-comparison-summary" aria-label="Choose teams">
        {([ ["First team", left, setLeft], ["Second team", right, setRight] ] as const).map(([label, id, setId]) =>
          <label key={label}>{label}<select value={id} onChange={event => setId(event.target.value)}>
            <option value="">Choose a team</option>
            {id && !teams.some(team => team.team.team_id === id) && <option value={id}>Unavailable selection</option>}
            {teams.map(({ team }) => <option key={team.team_id} value={team.team_id}>{team.display_name}</option>)}
          </select></label>)}
      </section>
      {invalid ? <p role="alert">Selected team is not in the current directory. Choose another team.</p>
        : same ? <p role="status">Choose two different teams.</p>
        : !left || !right ? <p role="status">Choose two teams to compare their corner samples.</p>
        : <ComparisonResults key={`${left}:${right}`} left={left} right={right} />}
    </> : <section className="panel ledger-message">No completed current-season fixtures are available.</section>)}
  </div>;
}
