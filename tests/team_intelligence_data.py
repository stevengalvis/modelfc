"""Synthetic corner-only fixtures; no production or provider data."""
from datetime import date, timedelta
from modelfc.team_intelligence import REGISTRY

HEADER = 'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n'
TODAY = date(2026, 10, 2)


def fixture_bytes(n=8, missing=None):
    rows = [HEADER]
    for day in range(n):
        for i in range(12):
            a, b = REGISTRY[i], REGISTRY[i+12]
            if day % 2:
                a, b = b, a
            w = 3 + (i % 6) + (3 if day >= 5 else 0)
            c = 2 + (i % 5)
            pair = ',' if missing == (day,i) else f'{w},{c}'
            rows.append(f'E1,{(date(2026,8,1)+timedelta(days=day*3)).strftime("%d/%m/%Y")},{a.source_name},{b.source_name},0,0,D,{pair}\n')
    return ''.join(rows).encode()


def representative_fixture_bytes():
    """One coherent four-team demo: long, early, short and missing-corner samples."""
    # A/B have 12 fixtures, C has eight and D has two. One late A/C match
    # lacks corners, making A's trend incomplete while B's is available.
    pairings = [('birmingham', 'cardiff')] * 7
    pairings += [('birmingham', 'portsmouth'), ('cardiff', 'portsmouth')] * 3
    pairings += [('birmingham', 'millwall'), ('birmingham', 'portsmouth'),
                 ('cardiff', 'millwall'), ('cardiff', 'portsmouth')]
    from modelfc.team_intelligence import BY_ID
    rows = [HEADER]
    for index, (home, away) in enumerate(pairings):
        if index % 2:
            home, away = away, home
        day = date(2026, 8, 1) + timedelta(days=index)
        corners = ',' if index == 14 else f'{3 + index % 7},{2 + index % 5}'
        rows.append(f'E1,{day.strftime("%d/%m/%Y")},{BY_ID[home].source_name},'
                    f'{BY_ID[away].source_name},0,0,D,{corners}\n')
    return ''.join(rows).encode()


def representative_population():
    import hashlib
    from modelfc.team_intelligence import calculate_population
    payload = representative_fixture_bytes()
    return calculate_population(payload, hashlib.sha256(payload).hexdigest(), TODAY)


if __name__ == '__main__':
    import json
    from pathlib import Path
    population = representative_population()
    destination = Path(__file__).parent / 'fixtures/team_intelligence'
    (destination / 'teams.json').write_text(json.dumps(
        population.list_team_intelligence().model_dump(mode='json'), indent=2) + '\n')
    (destination / 'profiles.json').write_text(json.dumps({
        team.team.team_id: population.get_team_profile(team.team.team_id).model_dump(mode='json')
        for team in population.summaries}, indent=2) + '\n')
