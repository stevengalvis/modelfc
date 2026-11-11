"""Synthetic adapter regressions; no new provider/history or publication path."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import goal_totals, pl_corner_odds
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.pl_goal_totals import from_pl_btts
from tests import test_epl_offline_integration as integration


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.harness = integration.EPLOfflineIntegrationTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        corners, btts = self.harness.prepare()
        self.btts = btts['forecasts'][0]
        identity = json.loads(self.btts.evidence)['fixture_id']
        self.corner = next(c for c in corners['forecasts'] if str(c['identity']['fixture_id']) == identity)
        self.rows = integration.saved_quotes([self.btts])
        self.quotes = integration.saved(self.rows)

    def corner_inputs(self):
        raw = encoded(self.corner)
        i = self.corner['identity']
        binding = encoded(dict(forecast_id=self.corner['forecast_sha256'], competition='E0',
            source_fixture_id=str(i['fixture_id']), quote_fixture_id='synthetic-odds-'+str(i['fixture_id']),
            home=i['home']['history_name'], away=i['away']['history_name'], kickoff=i['kickoff'], bookmaker='fanduel'))
        return raw, dict(forecast_sha256=fingerprint(raw), binding_raw=binding,
            binding_sha256=fingerprint(binding), team_side='HOME', line=4.5,
            as_of=integration.QUOTE_TIME, enabled=True)

    def test_disabled_and_no_refit_or_history_adapter(self):
        with self.assertRaisesRegex(ValueError, 'OPT_IN_REQUIRED'): from_pl_btts(self.btts)
        with self.assertRaisesRegex(ValueError, 'OPT_IN_REQUIRED'):
            pl_corner_odds.consume(None, None, forecast_sha256=None, binding_raw=None,
                binding_sha256=None, team_side='HOME', line=4.5, as_of=None)
        with patch.object(goal_totals, 'freeze', side_effect=AssertionError('refit')), patch(
                'modelfc.pl_btts.prepare', side_effect=AssertionError('history')):
            totals = from_pl_btts(self.btts, enabled=True)
            joined = integration.totals_join(totals, self.quotes)
        self.assertEqual(totals.origin_record, encoded(self.btts.wire()).decode())
        self.assertEqual(totals.history_sha256, self.btts.identity)
        self.assertEqual(totals.aggregates, self.btts.aggregates)
        self.assertEqual(totals, replace(totals))
        # Historical records remain readable after implementation changes.
        self.assertEqual(replace(totals, implementation_sha256='0'*64).origin_record, totals.origin_record)
        self.assertIsNone(joined['recommendation'])
        self.assertFalse(joined['live'])

    def test_totals_origin_reconstruction_cannot_lose_or_contradict_evidence(self):
        totals = from_pl_btts(self.btts, enabled=True)
        for changes in ({'origin_record': None}, {'history_sha256': '0'*64},
                {'sources': ('0'*64,)}, {'fixture': replace(totals.fixture, home='Wrong')},
                {'frozen_at': totals.frozen_at+timedelta(seconds=1)},
                {'cutoff': totals.cutoff-timedelta(days=1)}, {'home_rate': 99}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(totals, **changes)
        original = json.loads(totals.origin_record)
        for key in ('calendar_sha256', 'mapping_sha256', 'implementation_sha256', 'sources'):
            changed = deepcopy(original)
            if key == 'sources': changed['evidence'][key][0]['sha256'] = 'a'*64
            else: changed['evidence'][key] = 'a'*64
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(totals, origin_record=encoded(changed).decode())
        for changed in ('{}', 'x'*200001):
            with self.assertRaises(ValueError): replace(totals, origin_record=changed)
        with self.assertRaises(ValueError): goal_totals.from_btts(self.btts)  # E1 interface unchanged

    def test_corner_both_team_pairs_integer_push_and_unsupported_markets(self):
        for side in ('HOME', 'AWAY'):
            result = integration.corner_join(self.corner, self.quotes, team_side=side)
            self.assertEqual(result['forecast'], self.corner)
            self.assertEqual(len(result['consumer_implementation_sha256']), 64)
            self.assertFalse(result['prospective_capture']); self.assertIsNone(result['recommendation'])
            p = result['probabilities']
            self.assertAlmostEqual(p['over']+p['under']+p['equal'], 1)
        definitions = integration.metadata(); definitions[1]['handicap'] = 4
        result = integration.corner_join(self.corner, integration.saved(self.rows, definitions), line=4)
        self.assertGreater(result['probabilities']['equal'], 0)
        for side, line in (('MATCH', 9.5), ('FIRST_HALF', 4.5), ('HOME', 2.25), ('HOME', 1001), ('HOME', True)):
            with self.assertRaises(ValueError): integration.corner_join(self.corner, self.quotes, team_side=side, line=line)

    def test_corner_missing_stale_identity_partial_and_bad_metadata(self):
        for case in ('missing', 'stale', 'future_retrieval', 'kickoff', 'same_time', 'name', 'league',
                     'missing_side', 'inactive', 'undated', 'future_change', 'stale_change', 'bad_price',
                     'period', 'unknown_outcome', 'duplicate', 'payload_pin', 'metadata_pin'):
            with self.subTest(case=case):
                rows, definitions = deepcopy(self.rows), integration.metadata()
                opts = {}
                market = rows[0]['bookmakerOdds']['fanduel']['markets']['2']
                player = market['outcomes']['20']['players']['0']
                if case == 'missing': rows = []
                elif case == 'stale': opts['as_of'] = integration.QUOTE_TIME+timedelta(minutes=16)
                elif case == 'kickoff': rows[0]['startTime'] = (integration.NOW+timedelta(days=1)).isoformat()
                elif case == 'name': rows[0]['participant2Name'] = 'Wrong'
                elif case == 'league': rows[0]['tournamentId'] = 18
                elif case == 'missing_side': market['outcomes'].pop('21')
                elif case == 'inactive': market['marketActive'] = False
                elif case == 'undated': player.pop('changedAt')
                elif case == 'future_change': player['changedAt'] = (integration.QUOTE_TIME+timedelta(seconds=1)).isoformat()
                elif case == 'stale_change': player['changedAt'] = (integration.QUOTE_TIME-timedelta(minutes=16)).isoformat()
                elif case == 'bad_price': player['priceAmerican'] = '+1000'
                elif case == 'period': definitions[1]['period'] = 'p1'
                elif case == 'unknown_outcome': market['outcomes']['999'] = deepcopy(market['outcomes']['20'])
                elif case == 'duplicate':
                    definitions.append({**definitions[1], 'marketId': 99})
                    rows[0]['bookmakerOdds']['fanduel']['markets']['99'] = deepcopy(market)
                quotes = integration.saved(rows, definitions)
                if case == 'future_retrieval': quotes = replace(quotes, retrieved_at=integration.QUOTE_TIME+timedelta(seconds=1))
                if case == 'same_time': quotes = replace(quotes, retrieved_at=integration.NOW)
                if case == 'payload_pin': quotes = replace(quotes, payload_sha256='0'*64)
                if case == 'metadata_pin': quotes = replace(quotes, metadata_sha256='0'*64)
                with self.assertRaises(ValueError): integration.corner_join(self.corner, quotes, **opts)

    def test_corner_pinned_artifact_binding_and_structural_rejection(self):
        raw, opts = self.corner_inputs()
        for key in ('forecast_sha256', 'binding_sha256'):
            with self.assertRaises(ValueError): pl_corner_odds.consume(raw, self.quotes, **{**opts, key: '0'*64})
        for key in ('forecast_id', 'source_fixture_id', 'quote_fixture_id', 'home', 'away', 'competition', 'bookmaker', 'kickoff'):
            binding = json.loads(opts['binding_raw']); binding[key] = 'wrong'
            b = encoded(binding)
            with self.assertRaises(ValueError):
                pl_corner_odds.consume(raw, self.quotes, **{**opts, 'binding_raw': b, 'binding_sha256': fingerprint(b)})
        for mutate in (
            lambda f: f['prediction']['home'].update(expected_corners=-1),
            lambda f: f['prediction'].update(dispersion_size=0),
            lambda f: f['prediction']['fixture'].update(home_team='wrong'),
            lambda f: f['identity'].update(competition='E1'),
            lambda f: f.update(pre_kickoff_at_observation=False),
            lambda f: f['calendar'].update(bundle_sha256='bad'),
            lambda f: f['sources'][0].update(validation_scope='GOALS'),
        ):
            f = deepcopy(self.corner); mutate(f)
            f['forecast_sha256'] = fingerprint(encoded({k:v for k,v in f.items() if k != 'forecast_sha256'}))
            data = encoded(f)
            with self.assertRaises(ValueError):
                pl_corner_odds.consume(data, self.quotes, **{**opts, 'forecast_sha256': fingerprint(data)})
        for data in (b'{}', b'x'*200001):
            with self.assertRaises(ValueError):
                pl_corner_odds.consume(data, self.quotes, **{**opts, 'forecast_sha256': fingerprint(data)})
