"""Public recorded-price projection from a single validated inventory snapshot.

No acquisition, reforecasting or policy changes. A fixture uses its latest frozen
capture cohort (created_at, prediction_id), never the model with the highest EV.
All frozen forecasts remain listed; research forecasts are never priced here.
"""
import hashlib
import json
from datetime import datetime, timedelta

from modelfc.corner_market_intelligence import best_recommendations, rank_supported_offers
from modelfc.ledger_storage import LedgerError


def time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


# Bound the polling display. Complete immutable evidence remains in the ledger.
HISTORY_DAYS = 7
FIXTURE_LIMIT = 100


def fixture_key(prediction):
    fixture = prediction.get('fixture', prediction)
    return (fixture['competition'], fixture['home_team'], fixture['away_team'],
            time(fixture.get('kickoff_at', fixture.get('kickoff_utc'))).isoformat())


def display_keys(predictions, *, now):
    keys = {fixture_key(p) for p in predictions
            if time(p['created_at_utc']) <= now
            and time(fixture_key(p)[3]) >= now - timedelta(days=HISTORY_DAYS)}
    # Nearest upcoming first, then most recent history; identity breaks ties.
    return set(sorted(keys, key=lambda k: (
        time(k[3]) <= now,
        time(k[3]).timestamp() if time(k[3]) > now else -time(k[3]).timestamp(), k,
    ))[:FIXTURE_LIMIT])


def project(inventory, *, now):
    groups = {}
    for prediction in inventory['predictions']:
        if time(prediction['created_at_utc']) > now:
            continue
        key = (prediction['competition'], prediction['home_team'], prediction['away_team'],
               time(prediction['kickoff_utc']).isoformat())
        groups.setdefault(key, []).append(prediction)
    result = []
    for key, predictions in groups.items():
        competition, home, away, kickoff = key
        historical = time(kickoff) <= now or any(p['settlement_status'] == 'SETTLED' for p in predictions)
        forecasts = [{
            **{k: p[k] for k in ('prediction_id', 'created_at_utc', 'model_name', 'model_version')},
            'prediction_type': p.get('prediction_type', 'CORNER'),
            'research_only': p.get('research_status') == 'RESEARCH_ONLY',
            'expected_home_corners': p.get('expected_home_corners'),
            'expected_away_corners': p.get('expected_away_corners'),
            'btts_yes_probability': p.get('btts_yes_probability'),
        } for p in sorted(predictions, key=lambda p: (time(p['created_at_utc']), p['prediction_id']), reverse=True)]
        captures = [p for p in predictions if p['prediction_id'] in inventory['recorded']]
        primary = max(captures, key=lambda p: (time(p['created_at_utc']), p['prediction_id'])) if captures else None
        offers = []
        if primary:
            view = inventory['recorded'][primary['prediction_id']]
            raw = [offer for target in view['targets'] for offer in target['offers']
                   if offer['market_type'] == 'TEAM_TOTAL']
            winners = {(o['target_id'], o['bookmaker']) for o in best_recommendations(raw)}
            ranked = rank_supported_offers(raw)
            ranked += sorted((o for o in raw if o['status'] != 'SUPPORTED'),
                             key=lambda o: (o['target_id'], o['bookmaker']))
            # Current qualifying offers first; within each section use existing value ranking.
            ranked.sort(key=lambda o: (not o['recommendation_eligible'],
                                      o['current_availability'] == 'UNAVAILABLE' or o['status'] != 'SUPPORTED'))
            evidence = {(o['target_id'], o['observation_id'], o['bookmaker']): o['opportunity_id']
                        for o in inventory['opportunities'] if o['prediction_id'] == primary['prediction_id']}
            for offer in ranked:
                status = ('HISTORICAL' if historical else
                          'QUOTE_UNAVAILABLE' if offer['current_availability'] == 'UNAVAILABLE' else
                          'REVIEW_REQUIRED' if offer['status'] != 'SUPPORTED' else
                          'PRICE_NEEDS_RECHECKING' if not offer['current_eligible'] else
                          'RECENT_QUALIFYING' if offer['recommendation_eligible'] else
                          'VALUE_UNVERIFIED' if offer['no_vig_market_probability'] is None else
                          'NO_QUALIFYING_EDGE')
                offers.append({
                    **{k: primary[k] for k in ('competition', 'provider', 'provider_fixture_id',
                                              'kickoff_utc', 'home_team', 'away_team')},
                    **{k: offer[k] for k in (
                        'prediction_id', 'target_id', 'market_type', 'team_side', 'team', 'direction',
                        'line', 'bookmaker', 'american_odds', 'decimal_odds', 'retrieved_at_utc',
                        'observation_age_seconds', 'availability_checked_at_utc', 'model_probability',
                        'push_probability', 'decisive_model_probability', 'sportsbook_implied_probability',
                        'no_vig_market_probability', 'no_vig_probability_edge', 'expected_profit', 'qualified')},
                    'policy_version': view['qualification_policy']['version'],
                    'status': status,
                    'current_recommendation': not historical and (offer['target_id'], offer['bookmaker']) in winners,
                    'opportunity_id': evidence.get((offer['target_id'], offer['observation_id'], offer['bookmaker'])),
                })
        result.append({
            'fixture_id': hashlib.sha256(json.dumps(key).encode()).hexdigest(),
            'competition': competition, 'home_team': home, 'away_team': away, 'kickoff_utc': kickoff,
            'status': 'HISTORICAL' if historical else 'UPCOMING',
            'selected_prediction_id': primary['prediction_id'] if primary else None,
            'forecasts': forecasts, 'offers': offers,
        })
    result.sort(key=lambda f: (
        f['status'] == 'HISTORICAL',
        -time(f['kickoff_utc']).timestamp() if f['status'] == 'HISTORICAL' else time(f['kickoff_utc']).timestamp(),
        f['fixture_id'],
    ))
    return {'as_of_utc': now.isoformat(), 'fixtures': result}


def fixture_id_for(key):
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def upcoming_captures(predictions, *, now, fixture_id=None):
    """Select by recency before enriching any odds, targets, outcomes or research."""
    try:
        latest = {}
        for prediction in predictions:
            key = fixture_key(prediction)
            if time(key[3]) <= now or time(prediction['created_at_utc']) > now:
                continue
            if fixture_id is not None and fixture_id_for(key) != fixture_id:
                continue
            previous = latest.get(key)
            if previous is None or (time(prediction['created_at_utc']), prediction['prediction_id']) > (
                    time(previous['created_at_utc']), previous['prediction_id']):
                latest[key] = prediction
        keys = sorted(latest, key=lambda k: (time(k[3]), k))[:FIXTURE_LIMIT]
        return [latest[key] for key in keys]
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise LedgerError("INVALID_PROSPECTIVE_RECORD") from None


def project_shortlist(inventory, *, now, limit, ranking="best_value", on_offer=None):
    if ranking not in {"best_value", "higher_probability"}:
        raise ValueError("Invalid shortlist ranking")
    public = project(inventory, now=now)
    # Relax exactly one UI constraint. All actual engine flags remain unchanged;
    # unknown/unavailable, invalid, unqualified and nonpositive EV stay excluded.
    def observe(offer):
        if on_offer is not None:
            on_offer(offer)
        return True
    candidates = [dict(offer, recommendation_eligible=True)
                  for view in inventory['recorded'].values()
                  for target in view['targets'] for offer in target['offers']
                  if observe(offer) and not (set(offer['recommendation_ineligible_reasons']) - {'STALE_RETRIEVAL'})]
    available = {(o['prediction_id'], o['target_id'], o['bookmaker']): dict(o, fixture_id=f['fixture_id'])
                 for f in public['fixtures'] if f['status'] == 'UPCOMING' for o in f['offers']}
    ranked = [available[key] for offer in best_recommendations(candidates)
              if (key := (offer['prediction_id'], offer['target_id'], offer['bookmaker'])) in available]
    if ranking == "higher_probability":
        # Actual win mass, not win probability conditional on a non-push outcome.
        # Stable sorting retains the established value ranking and deterministic ties.
        ranked = sorted((offer for offer in ranked if offer['model_probability'] >= 0.65),
                        key=lambda offer: -offer['model_probability'])
    return {'as_of_utc': public['as_of_utc'], 'offers': ranked[:limit], 'total': len(ranked), 'limit': limit}
