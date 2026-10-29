"""Pinned SP1/I1 chronological final-data research; never a live consumer."""
import argparse
import csv
from io import StringIO
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from modelfc.btts_model import arithmetic_rates, btts_probabilities, MIN_HISTORY, MODEL_VERSION
from modelfc.btts_outcomes import scores
from modelfc.goal_totals import GoalAggregates
from modelfc.matches import Venue
from modelfc.providers.football_data import load_team_match_stats
from modelfc.providers.oddspapi_saved_response import _json

START = date(2025, 7, 1)
END = date(2026, 10, 22)
MIN_VENUE = 5
VERSION = 'sp1-i1-final-data-btts-chronological-v1'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('aware receipt/observation timestamp required')
    return result


@dataclass(frozen=True)
class Source:
    filename: str
    raw: bytes
    sha256: str
    retrieved_at: str | None = None


def load_sources(competition, sources, as_of):
    """Read a frozen byte snapshot through the existing GOALS-only strict loader."""
    if competition not in ('SP1', 'I1'):
        raise ValueError('research accepts only SP1/I1')
    observed = timestamp(as_of)
    if observed.date() <= END:
        raise ValueError('observation must follow fixed scoring window')
    if not sources or len(sources) > 5:
        raise ValueError('one to five season sources required')
    refs, rows, seen = [], [], set()
    with TemporaryDirectory() as directory:
        for source in sorted(sources, key=lambda s: s.filename):
            match = re.fullmatch(rf'{competition}_(22|23|24|25|26)(23|24|25|26|27)\.csv', source.filename)
            if not match or int(match[2]) != int(match[1]) + 1 or source.filename in seen:
                raise ValueError('duplicate or invalid season filename')
            seen.add(source.filename)
            if type(source.raw) is not bytes or not 0 < len(source.raw) <= 8_000_000:
                raise ValueError('source size/type rejected')
            if sha256(source.raw).hexdigest() != source.sha256:
                raise ValueError('source hash mismatch')
            retrieved = timestamp(source.retrieved_at) if source.retrieved_at is not None else None
            if retrieved is not None and retrieved > observed:
                raise ValueError('future source receipt')
            # The generic loader permits a caller-supplied league when Div is
            # absent. Research must establish native identity rather than fill it.
            reader = csv.DictReader(StringIO(source.raw.decode("utf-8-sig")))
            if not reader.fieldnames or "Div" not in reader.fieldnames or any(
                    row.get("Div") != competition for row in reader):
                raise ValueError("explicit native Div identity required")
            path = Path(directory) / source.filename
            path.write_bytes(source.raw)
            parsed = load_team_match_stats(path, competition=competition, validation_scope='GOALS')
            season = f'20{match[1]}/20{match[2]}'
            if any(r.season != season or retrieved is not None and r.match_date > retrieved.date() for r in parsed):
                raise ValueError('source season/receipt contradiction')
            rows.extend(r for r in parsed if r.venue is Venue.HOME)
            refs.append({'filename': source.filename, 'sha256': source.sha256,
                         'bytes': len(source.raw), 'retrieved_at': source.retrieved_at})
    keys = [(r.match_date, r.team, r.opponent) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate historical fixture')
    return sorted(rows, key=lambda r: (r.match_date, r.team, r.opponent)), refs


def evaluate(competition, sources, *, as_of):
    rows, refs = load_sources(competition, tuple(sources), as_of)
    # Entire day is scored before any of its outcomes enter aggregate history.
    home, away = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0])
    n = hg = ag = btts = 0
    latest = None
    outcomes, excluded = [], []
    for day, batch_iter in groupby(rows, key=lambda r: r.match_date):
        if day > END:
            break
        batch = list(batch_iter)
        for row in batch:
            if day < START:
                continue
            h, a = home[row.team], away[row.opponent]
            identity = {'date': day.isoformat(), 'home': row.team, 'away': row.opponent,
                        'season': row.season}
            reasons = []
            if n < MIN_HISTORY:
                reasons.append('LEAGUE_HISTORY_INSUFFICIENT')
            if h[0] < MIN_VENUE:
                reasons.append('HOME_VENUE_HISTORY_INSUFFICIENT')
            if a[0] < MIN_VENUE:
                reasons.append('AWAY_VENUE_HISTORY_INSUFFICIENT')
            if reasons:
                excluded.append({**identity, 'reasons': reasons, 'history_matches': n,
                                 'home_venue_matches': h[0], 'away_venue_matches': a[0]})
                continue
            aggregates = GoalAggregates(n, hg, ag, *h, *a)
            yes, no = btts_probabilities(*arithmetic_rates(aggregates))
            frozen = {'cutoff_date_exclusive': day.isoformat(), 'latest_history_date': latest,
                      'aggregates': asdict(aggregates)}
            outcomes.append({**identity, 'yes_probability': yes, 'no_probability': no,
                'baseline_yes_probability': btts/n,
                'actual_btts': 'YES' if row.goals_for > 0 and row.goals_against > 0 else 'NO',
                'frozen_inputs': frozen, 'frozen_inputs_sha256': sha256(encoded(frozen)).hexdigest()})
        for row in batch:
            h, a = home[row.team], away[row.opponent]
            for values, gf, ga in ((h, row.goals_for, row.goals_against), (a, row.goals_against, row.goals_for)):
                values[0] += 1
                values[1] += gf
                values[2] += ga
            n += 1
            hg += row.goals_for
            ag += row.goals_against
            btts += int(row.goals_for > 0 and row.goals_against > 0)
        latest = day.isoformat()
    def summary(selected):
        return {'model': scores(selected), 'baseline': scores([
            {**r, 'yes_probability': r['baseline_yes_probability']} for r in selected])}
    result = {'version': VERSION, 'model_version': MODEL_VERSION, 'competition': competition,
        'as_of': as_of, 'evaluation_kind': 'RETROSPECTIVE_FINAL_DATA_NOT_PROSPECTIVE',
        'validated': False, 'live': False, 'model_qualified': False,
        'calendar_required': False, 'historical_availability_proven': False,
        'receipt_semantics': 'SUPPLIED_TIMES_NOT_AUTHENTICATED',
        'sources': refs, 'input_manifest_sha256': sha256(encoded(refs)).hexdigest(),
        'scoring_window': {'start': START.isoformat(), 'end': END.isoformat()},
        'settings': {'min_history': MIN_HISTORY, 'min_venue_history': MIN_VENUE,
                     'baseline': 'earlier_league_empirical_btts_same_eligible_cohort'},
        'scored_count': len(outcomes), 'excluded_count': len(excluded),
        'metrics': summary(outcomes),
        'by_season': {season: summary([r for r in outcomes if r['season'] == season])
                      for season in ('2025/2026', '2026/2027')},
        'outcomes': outcomes, 'exclusions': excluded}
    result['report_sha256'] = sha256(encoded(result)).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--competition', required=True, choices=('SP1', 'I1'))
    parser.add_argument('--manifest', type=Path, required=True,
                        help='JSON list of path, filename, sha256, retrieved_at; relative paths use manifest directory')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = _json(args.manifest.read_bytes())
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 5:
        parser.error('manifest must contain one to five sources')
    sources = []
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {'path', 'filename', 'sha256', 'retrieved_at'}:
            parser.error('invalid source entry')
        path = args.manifest.parent / entry['path']
        if not path.is_file() or path.stat().st_size > 8_000_000:
            parser.error('source size/type rejected')
        sources.append(Source(entry['filename'], path.read_bytes(), entry['sha256'], entry['retrieved_at']))
    result = evaluate(args.competition, sources, as_of=args.as_of)
    with args.output.open('xb') as output:
        output.write(encoded(result))


if __name__ == '__main__':
    main()
