"""Offline repeatable reader comparison. Run with PYTHONPATH=src:.

Twelve frozen captures for one upcoming fixture, six lines, two books. Uses the
same validated on-disk fixture and denied live HTTP as the regression suite.
Reports reader work and serialized payload, not a production latency guarantee.
"""
import json
from statistics import median
from time import perf_counter
from unittest.mock import patch
from modelfc import corner_prospective_read as reader
from modelfc import corner_opportunities as evidence
from modelfc.providers import oddspapi as provider
from tests.test_recommendation_shortlist import ShortlistTests


def main():
    fixture = ShortlistTests()
    fixture.setUp()
    try:
        f = fixture.fixture
        for index in range(11):
            result, _ = provider.capture_quotes(f.data.capture.quotes,
                data_config_path=f.data.capture.config, state_dir=fixture.state,
                capture_key=f'benchmark-cohort-{index}')
            evidence.store_prediction_from_capture(fixture.state, result['analysis_id'])
        fixture.many_lines()
        before = f.data.snapshot()
        results = {}
        for label, read in (
            ('before_two_feeds', lambda: [reader.read_recommendations(fixture.state), reader.read_recorded_offers(fixture.state)]),
            ('after_shortlist', lambda: reader.read_shortlist(fixture.state)),
        ):
            timings = []
            for _ in range(7):
                with patch.object(reader, 'prediction_observations', wraps=reader.prediction_observations) as observations, \
                     patch.object(reader, 'market_intelligence_from_snapshot', wraps=reader.market_intelligence_from_snapshot) as intelligence:
                    start = perf_counter(); result = read(); timings.append((perf_counter()-start)*1000)
            results[label] = dict(median_ms=round(median(timings), 3),
                observation_reads=observations.call_count, intelligence_passes=intelligence.call_count,
                response_bytes=len(json.dumps(result).encode()))
        assert before == f.data.snapshot(), 'reader mutated state'
        print(json.dumps({'scenario': '12 cohorts / 1 fixture / 6 lines / 2 books', 'iterations': 7, **results}, indent=2))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
