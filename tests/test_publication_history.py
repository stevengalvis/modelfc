"""Synthetic saved evidence replay; no collection or production-state access."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from modelfc import published_recommendations as published, pick_strategies as strategies
from modelfc import corner_analysis_outcomes as outcomes
from modelfc.corner_prospective import _lock
from tests import test_published_recommendations as corner_fixtures
from tests import test_published_btts as btts_fixtures
from tests import test_settlement_reviews as void_fixtures


class PublicationHistoryTests(unittest.TestCase):
    def corner(self):
        fx = corner_fixtures.PublishedRecommendationTests(); fx.setUp()
        self.addCleanup(fx.doCleanups)
        fx.publish()
        return fx

    def test_publication_membership_boundary_empty_roi_and_readonly_replay(self):
        fx = self.corner(); cutoff = fx.now
        expected = published.read(fx.state)
        before = fx.fx.state_bytes()
        self.assertEqual(published.read(fx.state, as_of=cutoff), expected)
        empty = published.read(fx.state, as_of=cutoff-timedelta(microseconds=1))
        self.assertEqual(empty['summary']['picks'], 0)
        self.assertIsNone(empty['summary']['roi'])
        self.assertEqual(published.read(fx.state, as_of=cutoff.astimezone(timezone(timedelta(hours=3)))), expected)
        for invalid in (cutoff.replace(tzinfo=None), cutoff+timedelta(seconds=1), 'yesterday'):
            with self.assertRaisesRegex(ValueError, 'INVALID_PUBLICATION_READ_TIME'):
                published.read(fx.state, as_of=invalid)
        self.assertEqual(before, fx.fx.state_bytes())

    def test_corner_result_and_correction_do_not_leak_backwards(self):
        fx = self.corner(); publication = fx.now
        pending = published.read(fx.state, as_of=publication)
        first_time = publication + timedelta(days=2)
        with patch.object(outcomes, 'utc_timestamp', return_value=first_time.isoformat()):
            root = fx.fx.write_result(home=6, away=3)
        fx.now = first_time
        original = published.read(fx.state, as_of=first_time)
        fx.fx.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,1,12\n')
        corrected_time = first_time + timedelta(days=1)
        with patch.object(outcomes, 'utc_timestamp', return_value=corrected_time.isoformat()):
            outcomes.record_outcome(state_dir=fx.state, analysis_id=fx.fx.analysis_id,
                data_config_path=fx.fx.setup.config, idempotency_key='history-correction',
                supersedes_outcome_id=root['outcome_id'], correction_reason='Synthetic correction')
        fx.now = corrected_time
        before = fx.fx.state_bytes()
        self.assertEqual(published.read(fx.state, as_of=publication), pending)
        self.assertEqual(published.read(fx.state, as_of=first_time), original)
        corrected = published.read(fx.state, as_of=corrected_time)
        self.assertNotEqual(original['summary']['profit_units'], corrected['summary']['profit_units'])
        self.assertEqual([r['offer'] for r in original['publications']], [r['offer'] for r in corrected['publications']])
        self.assertEqual(before, fx.fx.state_bytes())

    def test_strategy_membership_precedes_filter_totals_and_pagination(self):
        fx = self.corner(); first = fx.now
        with _lock(fx.state): strategies.publish_locked(fx.state)
        expected = strategies.read(fx.state, scope='current', as_of=first, limit=1)
        fx.now += timedelta(days=2)
        self.assertEqual(strategies.read(fx.state, scope='current', as_of=first, limit=1), expected)
        empty = strategies.read(fx.state, scope='history', as_of=first-timedelta(microseconds=1))
        self.assertEqual((empty['total'], empty['totals']['picks'], empty['publications']), (0, 0, []))
        self.assertIsNone(empty['totals']['roi'])

    def test_btts_publication_and_later_outcome_replay(self):
        fx = btts_fixtures.PublishedBttsTests(); fx.setUp(); self.addCleanup(fx.doCleanups)
        fx.capture(); fx.publish(); first = fx.now
        expected = published.read(fx.state, as_of=first)
        from modelfc import btts_outcomes
        from modelfc.btts_research import digest
        fx.now = published._time(fx.forecast.fixture.kickoff_utc) + timedelta(hours=4)
        row = dict(Div='E1', Date='31/12/2026', Time='15:00', HomeTeam='A', AwayTeam='D',
                   FTHG='2', FTAG='1', FTR='H', HC='', AC='')
        source = dict(provider='football-data.co.uk', filename='E1_2627.csv', file_sha256='a'*64,
            row_number=2, row=row, row_sha256=digest(row), retrieved_at_utc=fx.now.isoformat())
        with patch.object(btts_outcomes, 'utc_timestamp', return_value=fx.now.isoformat()):
            btts_outcomes.settle(fx.state, digest(fx.forecast), source)
        self.assertEqual(published.read(fx.state, as_of=first), expected)
        settled_time = fx.now
        self.assertEqual(published.read(fx.state, as_of=settled_time)['summary']['wins'], 1)
        from modelfc import btts_corrections
        root = btts_outcomes._originals(fx.state, btts_outcomes.inventory(fx.state))
        fx.now += timedelta(days=1)
        row.update(FTHG='0', FTR='A')
        source.update(row_sha256=digest(row), retrieved_at_utc=fx.now.isoformat())
        with patch.object(btts_corrections, 'utc_timestamp', return_value=fx.now.isoformat()):
            btts_corrections.correct(fx.state, digest(fx.forecast), source,
                supersedes_record_hash=next(iter(root.values()))['record_hash'], reason='Synthetic correction')
        with patch.object(btts_corrections, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = fx.now
            self.assertEqual(published.read(fx.state, as_of=settled_time)['summary']['wins'], 1)
            self.assertEqual(published.read(fx.state, as_of=fx.now)['summary']['losses'], 1)

    def test_void_is_visible_only_from_recorded_review_time(self):
        fx = void_fixtures.SimulationVoidTests(); fx.setUp(); self.addCleanup(fx.doCleanups)
        first = fx.fx.now
        expected = published.read(fx.state, as_of=first)
        record, _ = fx.review()
        reviewed = published._time(record['recorded_at_utc'])
        fx.fx.now = reviewed
        self.assertEqual(published.read(fx.state, as_of=first), expected)
        self.assertEqual(published.read(fx.state, as_of=reviewed)['summary']['voids'], 1)
        self.assertEqual(published.read(fx.state, as_of=reviewed-timedelta(microseconds=1))['summary']['voids'], 0)

    def test_publications_sort_by_instant_across_offsets(self):
        fx = self.corner()
        row = published.read(fx.state)['publications'][0]
        older = {**row, 'publication_id': 'older', 'published_at_utc': '2026-09-19T14:00:00+03:00'}
        newer = {**row, 'publication_id': 'newer', 'published_at_utc': '2026-09-19T12:00:00Z'}
        self.assertEqual([r['publication_id'] for r in published._report([older, newer])['publications']], ['newer', 'older'])

    def test_nonchronological_corner_chain_rejects_historical_read(self):
        from modelfc import corner_prospective_read as reader
        from modelfc.ledger_storage import LedgerError
        fx = self.corner()
        records = [dict(outcome_id='root', supersedes_outcome_id=None, recorded_at_utc='2026-09-22T12:00:00Z'),
                   dict(outcome_id='tip', supersedes_outcome_id='root', recorded_at_utc='2026-09-21T12:00:00Z')]
        with patch.object(reader, 'load_outcome_chain_readonly', return_value=(records, records[1])):
            with self.assertRaisesRegex(LedgerError, 'INVALID_OUTCOME_CHRONOLOGY'):
                reader._outcome_tip(fx.state, fx.fx.prediction, as_of=fx.now)
