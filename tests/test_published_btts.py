"""Real saved BTTS evidence; no acquisition or network access."""
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from modelfc import published_recommendations as published, published_btts as btts
from modelfc import btts_outcomes as outcomes
from modelfc.btts_research import record_btts_research, digest
from modelfc.corner_api import create_app
from modelfc.corner_prospective import _lock
from modelfc.ledger_storage import LedgerError
from tests.test_btts_research import forecast, observation, NOW


class PublishedBttsTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.forecast = forecast()
        self.now = NOW + timedelta(seconds=30)
        self.enterContext(patch.object(published, "_now", side_effect=lambda: self.now))

    def capture(self, **kwargs):
        return record_btts_research(self.state, self.forecast, observation(**kwargs))

    def publish(self):
        with _lock(self.state):
            return published.publish_locked(self.state)

    def report(self):
        return published.read(self.state)

    def test_yes_best_book_frozen_once_and_readonly_api(self):
        self.capture()
        self.assertEqual(self.publish(), 1)
        first = self.report()
        row = first["publications"][0]
        self.assertEqual((row["offer"]["side"], row["offer"]["bookmaker"]), ("YES", "draftkings"))
        self.assertEqual(row["published_at_utc"], self.now.isoformat())
        self.assertEqual(row["ranking_views"], ["best_value", "higher_probability"])
        self.assertEqual(first["summary"]["picks"], first["summary"]["unique_fixtures"])
        self.assertEqual(first["by_league_market"][0]["market_type"], "BTTS")
        self.now += timedelta(minutes=10)
        self.capture(retrieved=self.now-timedelta(seconds=1), prices={"fanduel": (4., 4.)}, snapshot="later")
        self.assertEqual(self.publish(), 0)
        self.assertEqual(first, self.report())
        before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        response = TestClient(create_app(state_dir=self.state)).get('/api/v1/prospective/performance?view=model_picks')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), first)
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()})

    def test_no_selection_and_missing_optional_provider_stays_pending(self):
        self.capture(prices={"draftkings": (1.2, 5.), "fanduel": (1.2, 6.)})
        self.assertEqual(self.publish(), 1)
        row = self.report()["publications"][0]
        self.assertEqual((row["offer"]["side"], row["offer"]["american_odds"]), ("NO", 500))
        self.assertEqual(row["ranking_views"], ["best_value"])
        self.now += timedelta(days=150)
        with patch.dict('sys.modules', {'modelfc.sportmonks_outcome_evidence': None}):
            self.assertEqual(self.report()["summary"]["pending"], 1)

    def test_latest_unavailable_blocks_old_price_and_no_backfill(self):
        self.capture()
        self.capture(retrieved=NOW+timedelta(seconds=10), prices={}, snapshot="unavailable")
        self.assertEqual(self.publish(), 0)
        self.capture(retrieved=NOW+timedelta(seconds=20), snapshot="available")
        self.now = published._time(self.forecast.fixture.kickoff_utc)
        self.assertEqual(self.publish(), 0)
        self.assertEqual(self.report()["summary"]["picks"], 0)

    def test_thresholds_and_ranking_are_deterministic(self):
        source = (self.capture(),)
        selected = btts.select(source, self.now)
        self.assertEqual(selected, btts.select(tuple(reversed(source)), self.now))
        value = next(iter(selected.values()))[0]
        for change in ({"american_odds": -201}, {"no_vig_probability_edge": .049},
                       {"expected_profit": 0}, {"decimal_odds": 10}):
            self.assertFalse(btts.eligible(value | change))
        boundary = value | {"american_odds": -200, "decimal_odds": 1.5, "no_vig_probability_edge": .05}
        self.assertTrue(btts.eligible(boundary))

    def test_frozen_policy_and_tampering(self):
        self.capture(); self.publish()
        before = self.report()
        with patch('modelfc.corner_opportunities.DECIMAL_AMERICAN_ODDS_TOLERANCE', 0):
            self.assertEqual(before, self.report())
        path = next((self.state / published.DIRECTORY).glob('*.json'))
        record = json.loads(path.read_text())
        record['qualification_policy']['minimum_no_vig_edge'] = .01
        record['record_hash'] = digest({k:v for k,v in record.items() if k != 'record_hash'})
        path.write_text(json.dumps(record))
        with self.assertRaises(LedgerError): self.report()

    def test_goals_settlement_uses_original_price_and_rejects_conflicting_correction(self):
        self.capture(); self.publish()
        original = self.report()["publications"][0]
        self.now = published._time(self.forecast.fixture.kickoff_utc)+timedelta(hours=4)
        def source(home, away):
            row = dict(Div='E1', Date='31/12/2026', Time='15:00', HomeTeam='A', AwayTeam='D',
                       FTHG=str(home), FTAG=str(away), FTR='H' if home>away else 'A' if away>home else 'D', HC='', AC='')
            return dict(provider='football-data.co.uk', filename='E1_2627.csv', file_sha256='a'*64,
                row_number=2, row=row, row_sha256=digest(row), retrieved_at_utc=self.now.isoformat())
        with patch.object(outcomes, 'utc_timestamp', return_value=self.now.isoformat()):
            outcomes.settle(self.state, digest(self.forecast), source(2,1))
            settled = self.report()
            self.assertEqual(settled['summary']['wins'], 1)
            self.assertAlmostEqual(settled['summary']['profit_units'], 1.2)
            self.assertEqual(settled['publications'][0]['offer'], original['offer'])
            with self.assertRaisesRegex(LedgerError, 'RESULT_CONFLICT'):
                outcomes.settle(self.state, digest(self.forecast), source(0,1))
            self.assertEqual(settled, self.report())

    def test_no_pick_wins_only_when_at_least_one_team_does_not_score(self):
        self.capture(prices={"fanduel": (1.2, 6.)}); self.publish()
        self.now = published._time(self.forecast.fixture.kickoff_utc)+timedelta(hours=4)
        row = dict(Div='E1', Date='31/12/2026', Time='15:00', HomeTeam='A', AwayTeam='D',
                   FTHG='0', FTAG='1', FTR='A', HC='', AC='')
        source = dict(provider='football-data.co.uk', filename='E1_2627.csv', file_sha256='b'*64,
            row_number=2, row=row, row_sha256=digest(row), retrieved_at_utc=self.now.isoformat())
        with patch.object(outcomes, 'utc_timestamp', return_value=self.now.isoformat()), \
                patch.dict('sys.modules', {'modelfc.sportmonks_outcome_evidence': None}):
            outcomes.settle(self.state, digest(self.forecast), source)
            self.assertEqual(self.report()['summary']['profit_units'], 5)
            self.assertEqual(self.report()['summary']['wins'], 1)
            with patch.object(outcomes, '_load', side_effect=ImportError('optional decoder unavailable')):
                self.assertEqual(self.report()['summary']['pending'], 1)

    def test_higher_ev_side_wins_when_both_sides_qualify(self):
        source = (self.capture(prices={"draftkings": (4., 4.)}),)
        chosen = btts.select(source, self.now)
        self.assertEqual(len(chosen), 1)
        self.assertEqual(next(iter(chosen.values()))[0]['side'], 'YES')

    def test_crossed_kickoff_is_not_published(self):
        self.capture()
        kickoff = published._time(self.forecast.fixture.kickoff_utc)
        with patch.object(published, '_now', side_effect=[self.now, self.now, self.now, kickoff]):
            with self.assertRaisesRegex(LedgerError, 'PUBLICATION_WINDOW_CLOSED'):
                self.publish()
        self.assertEqual(self.report()['summary']['picks'], 0)
