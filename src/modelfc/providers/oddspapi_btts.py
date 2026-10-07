"""Pure OFFLINE BTTS adapter. Never fetches, captures, or imports an OddsPapi client.

The reviewed v4 /markets documentation identifies fulltime 'Both Teams To Score'
as marketType 'totals', handicap 0, sport 10, non-player, Yes/No outcomes.
IDs are resolved from that supplied dictionary, never guessed downstream.
"""

from modelfc.btts_market_data import (
    BOOKMAKERS, BookAvailability, BttsFixture, BttsObservation, BttsSelection,
    require_competition, timestamp, utc,
)
from modelfc.btts_research import digest
from modelfc.providers.oddspapi import (
    OddsPapiError, _array, _object, _selection_american, normalize_team, validate_fixture,
)


def normalize_btts(payload, metadata, fixture, *, competition, historical_names,
                   retrieved_at, as_of, max_age_seconds=300) -> BttsObservation:
    """Normalize one supplied response; preserve unknown coverage, with no older-price fallback."""
    require_competition(competition)
    try:
        observed = timestamp(retrieved_at)
        validate_fixture(fixture, as_of, competition)
        validate_fixture(payload, as_of, competition)
        fields = ("fixtureId", "participant1Id", "participant2Id", "participant1Name",
                  "participant2Name", "startTime", "tournamentId")
        if any(payload.get(k) != fixture.get(k) for k in fields):
            raise ValueError
        import math
        if (isinstance(max_age_seconds, bool) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0
                or not 0 <= (as_of - observed).total_seconds() <= max_age_seconds):
            raise ValueError
        dictionary = {}
        for item in _array(metadata):
            item = _object(item)
            mid = item.get("marketId")
            if type(mid) is not int or str(mid) in dictionary:
                raise ValueError
            dictionary[str(mid)] = item
        candidates = {}
        for mid, item in dictionary.items():
            if (item.get("marketName") != "Both Teams To Score" or item.get("marketType") != "totals"
                    or item.get("period") != "fulltime" or item.get("sportId") != 10
                    or item.get("playerProp") is not False or item.get("handicap") != 0):
                continue
            directions = {}
            for outcome in _array(item.get("outcomes")):
                outcome = _object(outcome)
                oid, name = outcome.get("outcomeId"), outcome.get("outcomeName")
                if type(oid) is not int or str(oid) in directions or name not in ("Yes", "No"):
                    raise ValueError
                directions[str(oid)] = name.upper()
            if len(directions) != 2 or set(directions.values()) != {"YES", "NO"}:
                raise ValueError
            candidates[mid] = directions
        canonical = BttsFixture(competition=competition, provider="oddspapi",
            provider_fixture_id=fixture["fixtureId"], kickoff_utc=utc(timestamp(fixture["startTime"])),
            home_team=normalize_team(fixture["participant1Name"], historical_names, competition),
            away_team=normalize_team(fixture["participant2Name"], historical_names, competition))
        selections, availability = [], []
        books = _object(payload.get("bookmakerOdds"))
        for book in BOOKMAKERS:
            status = "UNKNOWN"
            value = books.get(book)
            if value is not None:
                value = _object(value)
                if (value.get("bookmakerIsActive") is False or value.get("suspended") is True
                        or value.get("staleOdds") is True or payload.get("staleOdds") is True):
                    status = "UNAVAILABLE"
                elif value.get("bookmakerIsActive") is True and value.get("suspended") is False:
                    markets = _object(value.get("markets"))
                    matches = sorted(set(markets) & set(candidates))
                    if len(matches) > 1:
                        raise ValueError
                    if matches:
                        mid = matches[0]
                        market = _object(markets[mid])
                        if market.get("marketActive") is False or market.get("staleOdds") is True:
                            status = "UNAVAILABLE"
                        elif market.get("marketActive") is True:
                            outcomes = _object(market.get("outcomes"))
                            if set(outcomes) - set(candidates[mid]):
                                raise ValueError
                            pending, unusable = [], False
                            for oid, side in candidates[mid].items():
                                if oid not in outcomes:
                                    continue  # Missing does not prove withdrawal.
                                players = _object(_object(outcomes[oid]).get("players"))
                                price = players.get("0")
                                if price is None:
                                    continue
                                price = _object(price)
                                if price.get("active") is False or price.get("staleOdds") is True:
                                    unusable = True
                                    continue
                                if price.get("active") is not True:
                                    continue
                                pending.append(BttsSelection(competition=competition, provider="oddspapi",
                                    provider_fixture_id=canonical.provider_fixture_id, bookmaker=book, side=side,
                                    american_odds=_selection_american(price), decimal_odds=float(price["price"]),
                                    retrieved_at_utc=utc(observed),
                                    changed_at_utc=None if price.get("changedAt") is None else utc(timestamp(price["changedAt"])),
                                    provider_quote_reference=digest({"provider": "oddspapi", "fixture": fixture["fixtureId"],
                                        "bookmaker": book, "market_id": mid, "outcome_id": oid,
                                        "price": price, "retrieved_at_utc": utc(observed)})))
                            status = "UNAVAILABLE" if unusable else "AVAILABLE" if len(pending) == 2 else "UNKNOWN"
                            if status != "UNAVAILABLE":
                                selections.extend(pending)
            availability.append(BookAvailability(competition=competition, bookmaker=book, status=status))
        return BttsObservation(competition=competition, fixture=canonical, retrieved_at_utc=utc(observed),
            selections=tuple(selections), availability=tuple(availability),
            provider_snapshot_sha256=digest(payload), provider_metadata_sha256=digest(metadata))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise OddsPapiError("INVALID_BTTS_SNAPSHOT") from None
