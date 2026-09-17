import type { FieldErrors } from "@/lib/analysis-input";
import type { BetSide, MarketType, TeamSide } from "@/lib/api/types";
import type { EditableMarketInput } from "@/lib/parse-sportsbook-input";

interface Props {
  markets: EditableMarketInput[];
  errors: Record<string, FieldErrors>;
  unavailable: Record<string, string>;
  unavailableTypes: Partial<Record<MarketType, string>>;
  onChange: (markets: EditableMarketInput[]) => void;
}

export function MarketEditor({ markets, errors, unavailable, unavailableTypes, onChange }: Props) {
  function update(id: string, patch: Partial<EditableMarketInput>) {
    onChange(markets.map((market) => market.client_market_id === id
      ? { ...market, ...patch, parse_issue: null }
      : market));
  }

  return (
    <div className="market-editor">
      <div className="market-header" aria-hidden="true">
        <span>Market</span><span>Team</span><span>Side</span><span>Line</span><span>American odds</span><span />
      </div>
      {markets.map((market, index) => {
        const rowErrors = errors[market.client_market_id] ?? {};
        const issues = market.parse_issue ? [market.parse_issue] : Object.values(rowErrors);
        const issue = issues.join(" ");
        return (
          <div className={`market-row-wrap${issue ? " needs-attention" : ""}`} key={market.client_market_id}>
            <div className="market-source"><span>Source</span>{market.source_text}</div>
            <div className="market-row">
              <span className="row-number">{String(index + 1).padStart(2, "0")}</span>
              <label><span>Market</span>
                <select aria-label={`Market ${index + 1} type`} aria-invalid={Boolean(rowErrors.market_type)} value={market.market_type} onChange={(event) => {
                  const marketType = event.target.value as MarketType | "";
                  update(market.client_market_id, { market_type: marketType, team_side: marketType === "MATCH_TOTAL" ? "" : market.team_side });
                }}>
                  <option value="">Choose</option>
                  <option value="TEAM_TOTAL" disabled={Boolean(unavailableTypes.TEAM_TOTAL)}>Team total{unavailableTypes.TEAM_TOTAL ? " (unavailable)" : ""}</option>
                  <option value="MATCH_TOTAL" disabled={Boolean(unavailableTypes.MATCH_TOTAL)}>Match total{unavailableTypes.MATCH_TOTAL ? " (unavailable)" : ""}</option>
                </select>
              </label>
              <label><span>Team</span>
                <select aria-label={`Market ${index + 1} team`} aria-invalid={Boolean(rowErrors.team_side)} disabled={market.market_type === "MATCH_TOTAL"} value={market.market_type === "MATCH_TOTAL" ? "MATCH" : market.team_side} onChange={(event) => update(market.client_market_id, { team_side: event.target.value as TeamSide })}>
                  <option value="">Choose</option>
                  {market.market_type === "MATCH_TOTAL" ? <option value="MATCH">Both teams</option> : null}
                  <option value="HOME">Home</option><option value="AWAY">Away</option>
                </select>
              </label>
              <label><span>Side</span>
                <select aria-label={`Market ${index + 1} side`} aria-invalid={Boolean(rowErrors.side)} value={market.side} onChange={(event) => update(market.client_market_id, { side: event.target.value as BetSide })}>
                  <option value="">Choose</option><option value="OVER">Over</option><option value="UNDER">Under</option>
                </select>
              </label>
              <label><span>Line</span>
                <input aria-invalid={Boolean(rowErrors.line)} aria-label={`Market ${index + 1} line`} inputMode="decimal" value={market.line} onChange={(event) => update(market.client_market_id, { line: event.target.value })} />
              </label>
              <label><span>American odds</span>
                <input aria-invalid={Boolean(rowErrors.american_odds)} aria-label={`Market ${index + 1} American odds`} inputMode="numeric" value={market.american_odds} onChange={(event) => update(market.client_market_id, { american_odds: event.target.value })} />
              </label>
              <button className="remove-button" aria-label={`Remove market ${index + 1}`} type="button" onClick={() => onChange(markets.filter((item) => item.client_market_id !== market.client_market_id))}>×</button>
            </div>
            {issue ? <p className="row-issue" role="status">{issue}</p> : null}
            {unavailable[market.client_market_id] ? <p className="row-issue">Not analyzed: {unavailable[market.client_market_id]}</p> : null}
          </div>
        );
      })}
    </div>
  );
}
