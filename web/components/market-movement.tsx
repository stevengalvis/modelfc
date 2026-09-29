import type { OpportunityDetail } from "@/lib/api/types";

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
const odds = (value: number) => value > 0 ? `+${value}` : String(value);
const time = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC";

export function MarketMovement({ detail }: { detail: OpportunityDetail }) {
  const snapshots = detail.recorded_market;
  const comparable = snapshots.filter((item) => item.no_vig_market_probability !== null);
  const zeno = detail.model_decisive_probability;
  const values = [zeno, ...comparable.map((item) => item.no_vig_market_probability!)];
  const low = Math.max(0, Math.min(...values) - 0.04);
  const high = Math.min(1, Math.max(...values) + 0.04);
  const start = Date.parse(snapshots[0].retrieved_at_utc);
  const end = Date.parse(snapshots[snapshots.length - 1].retrieved_at_utc);
  const x = (date: string) => end === start ? 40 : 40 + 520 * (Date.parse(date) - start) / (end - start);
  const y = (value: number) => 180 - 140 * (value - low) / (high - low);
  const status = detail.market_movement.status;
  const change = detail.market_movement.market_change_percentage_points;
  const summary = status === "NO_LATER_OBSERVATION" ? "No later pre-kickoff observation was recorded."
    : status === "UNAVAILABLE" ? "Later prices were recorded, but no later paired market was available for comparison."
    : status === "UNCHANGED" ? "The latest recorded market remained the same distance from Zeno."
    : `Market moved ${Math.abs(change!).toFixed(1)}pp ${status === "TOWARD_ZENO" ? "toward" : "away from"} Zeno after qualification.`;

  return <div className="movement-content">
    <p className="movement-summary">{summary}</p>
    <div className="movement-chart">
      <svg viewBox="0 0 600 220" role="img" aria-label="Recorded market no-vig probability by observation time, with Zeno's frozen decisive probability as a reference">
        <line x1="40" x2="560" y1={y(zeno)} y2={y(zeno)} className="movement-zeno" />
        <text x="40" y="20" className="movement-label">Zeno {percent(zeno)}</text>
        {snapshots.slice(1).map((item, index) => {
          const previous = snapshots[index];
          return !(index === 0 && detail.recorded_market_count > snapshots.length)
            && item.no_vig_market_probability !== null && previous.no_vig_market_probability !== null
            ? <line key={`${item.observation_id}-line`} x1={x(previous.retrieved_at_utc)} y1={y(previous.no_vig_market_probability)}
                x2={x(item.retrieved_at_utc)} y2={y(item.no_vig_market_probability)} className="movement-market" /> : null;
        })}
        {comparable.map((item) => <circle key={item.observation_id} cx={x(item.retrieved_at_utc)}
          cy={y(item.no_vig_market_probability!)} r="5" className="movement-point">
          <title>{time(item.retrieved_at_utc)} · Market {percent(item.no_vig_market_probability!)} · {odds(item.american_odds)}</title>
        </circle>)}
        <text x="40" y="210" className="movement-label">{time(snapshots[0].retrieved_at_utc)}</text>
        {end !== start && <text x="560" y="210" textAnchor="end" className="movement-label">{time(snapshots[snapshots.length - 1].retrieved_at_utc)}</text>}
      </svg>
    </div>
    <ol className="detail-timeline movement-snapshots">{snapshots.map((item) => <li key={item.observation_id}>
      <span>{time(item.retrieved_at_utc)}</span>
      <strong>{odds(item.american_odds)} ({item.decimal_odds.toFixed(2)}) · Market no-vig {item.no_vig_market_probability === null ? "—" : percent(item.no_vig_market_probability)}</strong>
      {item.qualifying_observation && <small>Qualification</small>}
      {!item.price_consistent && <small className="detail-price-warning">Provider price discrepancy · review required</small>}
      {item.no_vig_market_probability === null && <small>Unpaired quote · excluded from chart</small>}
    </li>)}</ol>
    <p className="ledger-meta">Recorded pre-kickoff snapshots. Not live or closing odds. Lines join recorded points only; gaps are not filled.</p>
    {detail.recorded_market_count > snapshots.length && <p className="ledger-meta">Showing {snapshots.length} of {detail.recorded_market_count} comparable snapshots.</p>}
  </div>;
}
