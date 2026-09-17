import { competitionIssue, marketLabels } from "@/lib/api/capabilities";
import type { CompetitionCapability } from "@/lib/api/types";

export function CapabilityFeedback({ competition }: { competition: CompetitionCapability }) {
  const issue = competitionIssue(competition);
  return (
    <div className={`capability-feedback${issue ? " blocked" : ""}`}>
      <p><strong>{issue?.title ?? `${competition.name}: analysis available`}</strong></p>
      <ul className="capability-warnings">
        {competition.warnings.map((warning) => <li key={warning.code}><b>{warning.code}</b>: {warning.message}</li>)}
      </ul>
      <details>
        <summary>Data and refresh details</summary>
        <dl>
          <div><dt>Enabled markets</dt><dd>{competition.markets.map((type) => marketLabels[type]).join(", ") || "None"}</dd></div>
          <div><dt>Latest result</dt><dd>{competition.latest_result_date ?? "Not available"}{competition.latest_result_date && competition.stale ? " · Stale" : ""}</dd></div>
          <div><dt>Last refresh attempt</dt><dd>{competition.last_refresh_at ?? "Not recorded"}</dd></div>
          <div><dt>Attempt status</dt><dd>{competition.last_refresh_status ?? "Not recorded"}</dd></div>
          <div><dt>Refresh implementation</dt><dd>{competition.automatic_refresh ? "Available" : "Unavailable"}</dd></div>
          <div><dt>Scheduled refresh</dt><dd>{competition.refresh_job_status}</dd></div>
        </dl>
        <p>Refresh support does not establish that a schedule is running.</p>
      </details>
    </div>
  );
}
