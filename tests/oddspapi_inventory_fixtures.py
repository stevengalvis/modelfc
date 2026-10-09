"""Synthetic offline market inventory fixtures, shared with metadata tests."""
from datetime import datetime, timezone

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def metadata():
    rows = (
        (1, "Corners - Over Under Full Time", "totals-corners", "fulltime", 9.5, ("Over", "Under")),
        (2, "Corners - Over Under Team 1", "teamtotals-corners-team1", "fulltime", 4.5, ("Over", "Under")),
        (3, "Corners - Over Under Team 2", "teamtotals-corners-team2", "fulltime", 4.5, ("Over", "Under")),
        (4, "Corners - Over Under First Half", "totals-corners", "p1", 4.5, ("Over", "Under")),
        (5, "Both Teams To Score", "totals", "fulltime", 0, ("Yes", "No")),
        (6, "Over Under", "totals", "fulltime", 2.5, ("Over", "Under")),
        (7, "Over Under Team 1", "teamtotals-team1", "fulltime", 1.5, ("Over", "Under")),
        (8, "Over Under Team 2", "teamtotals-team2", "fulltime", 1.5, ("Over", "Under")),
    )
    return [{"marketId": mid, "marketName": name, "marketType": kind, "period": period,
             "handicap": line, "sportId": 10, "playerProp": False,
             "outcomes": [{"outcomeId": mid * 10 + index, "outcomeName": side}
                          for index, side in enumerate(sides)]}
            for mid, name, kind, period, line, sides in rows]


def market(mid, *, active=True, stale=False, complete=True, main=True):
    outcomes = {}
    for index in range(2 if complete else 1):
        outcomes[str(mid * 10 + index)] = {"players": {"0": {
            "active": active, "staleOdds": stale, "price": 1.9 + index / 10,
            "priceAmerican": "-110", "mainLine": main,
            "changedAt": "2026-10-08T11:59:00Z",
        }}}
    return {"marketActive": active, "staleOdds": stale, "outcomes": outcomes}


def payload():
    return [{"fixtureId": "id-fixture-1", "sportId": 10, "tournamentId": 18,
             "statusId": 0, "hasOdds": True, "startTime": "2026-10-10T15:00:00Z",
             "bookmakerOdds": {
                 "draftkings": {"bookmakerIsActive": True, "suspended": False,
                                  "markets": {str(mid): market(mid) for mid in range(1, 9)}},
                 "fanduel": {"bookmakerIsActive": True, "suspended": False,
                              "markets": {"5": market(5), "6": market(6, main=False)}},
             }}]
