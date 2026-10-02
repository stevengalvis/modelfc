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
