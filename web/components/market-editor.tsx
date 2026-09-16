import type { Dispatch, SetStateAction } from "react";
import type { BetSide, MarketType, TeamSide } from "@/lib/api/types";

export interface EditableMarket {
  client_market_id: string;
  market_type: MarketType;
  team_side: TeamSide | null;
  side: BetSide;
  line: string;
  american_odds: string;
}

interface Props {
  markets: EditableMarket[];
  setMarkets: Dispatch<SetStateAction<EditableMarket[]>>;
}

export function MarketEditor({ markets, setMarkets }: Props) {
  function update(id: string, patch: Partial<EditableMarket>) {
    setMarkets((current) => current.map((market) => market.client_market_id === id ? { ...market, ...patch } : market));
  }

  return (
    <div className="market-editor">
      <div className="market-header" aria-hidden="true">
        <span>Market</span><span>Team</span><span>Side</span><span>Line</span><span>American odds</span><span />
      </div>
      {markets.map((market, index) => (
        <div className="market-row" key={market.client_market_id}>
          <span className="row-number">{String(index + 1).padStart(2, "0")}</span>
          <label><span>Market</span>
            <select value={market.market_type} onChange={(event) => {
              const marketType = event.target.value as MarketType;
              update(market.client_market_id, { market_type: marketType, team_side: marketType === "MATCH_TOTAL" ? null : "HOME" });
            }}>
              <option value="TEAM_TOTAL">Team total</option><option value="MATCH_TOTAL">Match total</option>
            </select>
          </label>
          <label><span>Team</span>
            <select disabled={market.market_type === "MATCH_TOTAL"} value={market.team_side ?? "MATCH"} onChange={(event) => update(market.client_market_id, { team_side: event.target.value as TeamSide })}>
              {market.market_type === "MATCH_TOTAL" && <option value="MATCH">Both teams</option>}
              <option value="HOME">Home</option><option value="AWAY">Away</option>
            </select>
          </label>
          <label><span>Side</span>
            <select value={market.side} onChange={(event) => update(market.client_market_id, { side: event.target.value as BetSide })}>
              <option value="OVER">Over</option><option value="UNDER">Under</option>
            </select>
          </label>
          <label><span>Line</span>
            <input aria-label={`Market ${index + 1} line`} inputMode="decimal" value={market.line} onChange={(event) => update(market.client_market_id, { line: event.target.value })} />
          </label>
          <label><span>American odds</span>
            <input aria-label={`Market ${index + 1} American odds`} inputMode="numeric" value={market.american_odds} onChange={(event) => update(market.client_market_id, { american_odds: event.target.value })} />
          </label>
          <button className="remove-button" aria-label={`Remove market ${index + 1}`} disabled={markets.length === 1} type="button" onClick={() => setMarkets(markets.filter((item) => item.client_market_id !== market.client_market_id))}>×</button>
        </div>
      ))}
    </div>
  );
}
