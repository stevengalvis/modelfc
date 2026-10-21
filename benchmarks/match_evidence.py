"""Offline synthetic comparison; run with PYTHONPATH=src:. python benchmarks/match_evidence.py."""
import json
import hashlib
from statistics import median
from time import perf_counter

from modelfc.btts_market_data import BttsFixture
from modelfc.btts_research import read_btts_research
from modelfc.corner_research import inspect_fixture
from modelfc.match_evidence import match_evidence
from modelfc.team_intelligence import BY_SOURCE, calculate_population
from tests import test_corner_research
from tests.team_intelligence_data import fixture_bytes


def main():
    harness = test_corner_research.ResearchTests()
    harness.setUp()
    try:
        harness.run_pilot()
        snapshot = harness.snapshot()
        prediction = harness.manifest(snapshot)['entries'][0]['prediction']['id']
        detail = inspect_fixture(harness.state, snapshot, prediction)
        saved = detail['fixture']
        fixture = BttsFixture(**{k: saved[k] for k in ('competition', 'provider', 'provider_fixture_id', 'home_team', 'away_team')},
                              kickoff_utc=saved['kickoff_at'])
        payload = fixture_bytes()

        def baseline():
            # Analogous four source-level detail calls, not browser/network timing.
            return [inspect_fixture(harness.state, snapshot, prediction),
                    [v.model_dump(mode='json') for v in read_btts_research(harness.state, as_of=harness.now)],
                    *[calculate_population(payload, hashlib.sha256(payload).hexdigest(), harness.now.date()).get_team_profile(
                        BY_SOURCE[name].team_id).model_dump(mode='json') for name in (fixture.home_team, fixture.away_team)]]

        def compact():
            return match_evidence(harness.state, fixture, as_of=harness.now,
                                  snapshot_id=snapshot, prediction_id=prediction, history_bytes=payload)

        reports = {}
        for name, operation, calls in [('detail_workflow', baseline, 4), ('compact', compact, 1)]:
            durations = []
            for _ in range(20):
                start = perf_counter()
                result = operation()
                durations.append((perf_counter() - start) * 1000)
            reports[name] = {'median_ms': round(median(durations), 3), 'source_interface_calls': calls,
                             'json_bytes': len(json.dumps(result, separators=(',', ':'), ensure_ascii=True).encode())}
        print(json.dumps({'scope': 'synthetic offline; empty BTTS inventory; not browser, production or token measurement',
                          'iterations': 20, 'results': reports}, indent=2))
    finally:
        harness.doCleanups()


if __name__ == '__main__':
    main()
