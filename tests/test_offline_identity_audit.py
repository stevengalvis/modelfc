"""Synthetic identity evidence only; no private capture or historical CSV claimed."""
from contextlib import redirect_stdout
from datetime import timedelta
import hashlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from modelfc.offline_identity_audit import audit_identities, main, IdentityAuditError
from modelfc.offline_forecasts import KnownFixture, prepare_forecast, consume_forecast
from modelfc.providers.oddspapi import normalize_team, OddsPapiError
from tests.test_shared_odds import sources, snapshot, known
from tests.test_oddspapi_saved_response import saved_fixture
from tests.oddspapi_inventory_fixtures import NOW


def fixture(home='Home', away='Away', hid=1, aid=2, fid='fixture-1', tid=18):
    row = saved_fixture(tid, fid)
    row.update(participant1Name=home, participant2Name=away, participant1Id=hid,
               participant2Id=aid, startTime=(NOW+timedelta(hours=24)).isoformat())
    return row


def history(home='Home', away='Away', extra=''):
    name, content = sources(extra)[0]
    header, body = content.split(b'\n', 1)
    return ((name, header+b'\n'+body.replace(b'Home', home.encode()).replace(b'Away', away.encode())),)


def audit(rows=None, data=None):
    return audit_identities(json.dumps([fixture()] if rows is None else rows).encode(),
                            sources() if data is None else data, cutoff=NOW.date(), as_of=NOW)


class OfflineIdentityAuditTests(TestCase):
    def test_verified_aliases_join_unknown_identities_still_fail_closed(self):
        report = audit([fixture('West Ham United', 'Queens Park Rangers'),
                        fixture('West Ham United', 'Unknown', 1, 3, 'other')],
                       history('West Ham', 'QPR'))
        self.assertEqual(len(report['identities']), 3)
        self.assertEqual(len(report['unmatched_identities']), 1)
        self.assertEqual(report['unmatched_identities'][0]['provider_name'], 'Unknown')
        self.assertEqual(report['historical_names'], ['QPR', 'West Ham'])
        self.assertIn('IDENTITY_UNVERIFIED', report['eligible_fixtures'][1]['failures'])
        for name in ('Unknown', 'West Ham united', 'Queens Park Rangers FC'):
            with self.assertRaises(OddsPapiError): normalize_team(name, {'West Ham', 'QPR'}, 'E1')

    def test_existing_verified_aliases_and_provider_history_join(self):
        for provider, canonical in (('Wolverhampton Wanderers', 'Wolves'),
                                    ('West Bromwich Albion', 'West Brom'),
                                    ('Norwich City', 'Norwich'), ('Bolton Wanderers', 'Bolton')):
            report = audit([fixture(provider)], history(canonical))
            self.assertEqual(report['unmatched_identities'], [])
            identity = next(i for i in report['identities'] if i['provider_team_id'] == 1)
            self.assertEqual(identity['historical_name'], canonical)
            self.assertEqual(identity['competition'], 'E1')
            self.assertEqual(report['eligible_fixtures'][0]['status'], 'COUNT_GATES_SATISFIED')

    def test_ambiguous_provider_id_or_multiple_ids_for_canonical_rejected(self):
        for rows in ([fixture(), fixture('Unknown', 'Away', 1, 2, 'other')],
                     [fixture(), fixture(hid=3, fid='other')]):
            report = audit(rows)
            self.assertIn('AMBIGUOUS_PROVIDER_IDENTITY', {i['status'] for i in report['unmatched_identities']})
            self.assertTrue(all('AMBIGUOUS_PROVIDER_IDENTITY' in f['failures'] for f in report['eligible_fixtures']))

    def test_alias_exact_source_collision_is_review_not_silent_choice(self):
        extra = 'E1,01/10/2026,Wolverhampton Wanderers,Away,1,1,4,3\n'
        report = audit([fixture('Wolverhampton Wanderers')], history('Wolves', extra=extra))
        self.assertEqual(report['unmatched_identities'][0]['status'], 'AMBIGUOUS_HISTORY_IDENTITY')
        self.assertIsNone(report['unmatched_identities'][0]['historical_name'])

    def test_shared_corner_and_btts_consumers_use_existing_resolver(self):
        base = known()
        f = KnownFixture(base.identity, 'Wolves', 'West Brom')
        prepared = prepare_forecast(f, history('Wolves', 'West Brom'),
            frozen_at=NOW-timedelta(seconds=1), cutoff=NOW.date())
        snap = snapshot([fixture('Wolverhampton Wanderers', 'West Bromwich Albion')])
        result = consume_forecast(prepared, snap, snap.fixtures[0])
        self.assertEqual(len(result.corner), 4)
        self.assertEqual(len(result.btts), 1)
        self.assertEqual(prepared.btts.fixture.home_team, 'Wolves')

    def test_cutoff_excludes_same_day_future_results_from_gates(self):
        extra = 'E1,08/10/2026,Home,Away,99,99,99,99\nE1,09/10/2026,Home,Away,99,99,99,99\n'
        before, after = audit(), audit(data=sources(extra))
        self.assertEqual(before['eligible_fixtures'], after['eligible_fixtures'])
        self.assertEqual(after['excluded_same_day_future_observations'], 4)
        self.assertNotEqual(before['history_sources'], after['history_sources'])

    def test_existing_minimum_history_and_venue_requirements_unchanged(self):
        name, raw = sources()[0]
        sparse = ((name, b'\n'.join(raw.splitlines()[:4])+b'\n'),)
        result = audit(data=sparse)['eligible_fixtures'][0]
        self.assertIn('INSUFFICIENT_LEAGUE_OBSERVATIONS', result['failures'])
        self.assertIn('INSUFFICIENT_TEAM_VENUE_OBSERVATIONS', result['failures'])
        self.assertEqual(result['minimum_league_team_observations'], 100)
        self.assertEqual(result['minimum_team_venue_observations'], 5)
        with self.assertRaises(ValueError):
            prepare_forecast(known(), sparse, frozen_at=NOW, cutoff=NOW.date(), models=('corner',))
        # Sufficient league history cannot substitute for the team's venue sample.
        other = raw.replace(b'Home', b'Other').replace(b'Away', b'Else')
        thin = raw.splitlines()[:4]
        thin.extend(other.splitlines()[4:])
        result = audit(data=((name, b'\n'.join(thin)+b'\n'),))['eligible_fixtures'][0]
        self.assertNotIn('INSUFFICIENT_LEAGUE_OBSERVATIONS', result['failures'])
        self.assertIn('INSUFFICIENT_TEAM_VENUE_OBSERVATIONS', result['failures'])

    def test_stale_is_warning_promotion_not_inferred(self):
        result = audit()['eligible_fixtures'][0]
        self.assertEqual(result['failures'], [])
        self.assertTrue(all(t['stale_history_warning'] for t in result['teams']))
        self.assertTrue(all(t['promotion_status'] == 'NOT_VERIFIED' for t in result['teams']))

    def test_staleness_matches_existing_fixture_date_warning(self):
        latest = NOW.date()-timedelta(days=14)
        name, raw = sources()[0]
        extra = f'E1,{latest:%d/%m/%Y},Home,Away,2,1,4,3\n'
        result = audit(data=((name,raw+extra.encode()),))['eligible_fixtures'][0]
        self.assertEqual(result['teams'][0]['history_age_days'], 15)
        self.assertTrue(result['teams'][0]['stale_history_warning'])
        self.assertEqual(result['failures'], [])
        from modelfc.corner_analysis import _team_freshness_warnings
        prepared = prepare_forecast(known(), ((name,raw+extra.encode()),), frozen_at=NOW,
                                    cutoff=NOW.date(), models=('corner',))
        self.assertIn('TEAM_HISTORY_AGE', {w.code for w in _team_freshness_warnings(
            known().identity.kickoff_utc.date(), prepared.corner.home, 14)})

    def test_exact_source_identity_without_prior_records_reported_separately(self):
        extra = 'E1,08/10/2026,New,Away,1,1,4,3\n'
        result = audit([fixture('New')], sources(extra))['eligible_fixtures'][0]
        self.assertIn('MISSING_PRE_CUTOFF_HISTORICAL_RECORDS', result['failures'])
        self.assertEqual(result['teams'][0]['team_observations'], 0)
        self.assertIsNone(result['teams'][0]['latest_history_date'])

    def test_all_pairings_inventoried_only_window_fixtures_diagnosed(self):
        rows = [fixture(), fixture('Unknown', fid='outside')]
        rows[1]['startTime'] = (NOW+timedelta(hours=30)).isoformat()
        report = audit(rows)
        self.assertEqual(len(report['eligible_fixtures']), 1)
        self.assertIn('Unknown', {i['provider_name'] for i in report['identities']})

    def test_supported_registry_other_models_not_enabled(self):
        report = audit([fixture(tid=tid, fid=str(tid)) for tid in (18,17,8,23,242)])
        self.assertEqual(report['unsupported_model_competitions'], ['E0','I1','MLS','SP1'])
        self.assertEqual(len(report['eligible_fixtures']), 1)
        with self.assertRaises(IdentityAuditError): audit([fixture(tid=325)])
        for key, value in (('participant1Id', True), ('participant1Id', 0),
                           ('participant1Id', 2), ('tournamentSlug', 'wrong'),
                           ('categorySlug', 'brazil'), ('startTime', '2026-10-09T12:00:00')):
            row = fixture(); row[key] = value
            with self.assertRaisesRegex(IdentityAuditError, '^IDENTITY_AUDIT_REJECTED$'): audit([row])

    def test_deterministic_hash_pinned_export_no_io_or_budget(self):
        rows = [fixture(), fixture(fid='other')]
        data = sources()
        with patch('socket.socket', side_effect=AssertionError('network')), \
             patch('modelfc.ledger_storage.write_new_record', side_effect=AssertionError('write')), \
             patch('modelfc.corner_prospective._RequestBudgetGuard.reserve', side_effect=AssertionError('budget')):
            report = audit(rows, data)
        self.assertEqual(report, audit(rows, data))
        self.assertEqual(report['identities'], audit(list(reversed(rows)), data)['identities'])
        self.assertEqual(report['source_payload_sha256'], hashlib.sha256(json.dumps(rows).encode()).hexdigest())
        self.assertEqual(report['provider_requests'], 0)
        self.assertTrue(report['retrospective_diagnostic_only'])
        self.assertNotIn('bookmakerOdds', json.dumps(report))

    def test_malformed_duplicate_source_and_fixture_fail_closed(self):
        name, raw = sources()[0]
        for data in (((name, b'PRIVATE-MARKER'),), sources()+sources(),
                     ((name, raw+raw.splitlines()[1]+b'\n'),), (('SP1_2627.csv', raw),)):
            with self.assertRaisesRegex(IdentityAuditError, '^IDENTITY_AUDIT_REJECTED$'): audit(data=data)
        with self.assertRaises(IdentityAuditError): audit([fixture(), fixture()])
        for raw in (b'{"sportId":10,"sportId":10}', b'NaN', b'[null]'):
            with self.assertRaisesRegex(IdentityAuditError, '^IDENTITY_AUDIT_REJECTED$'):
                audit_identities(raw, sources(), cutoff=NOW.date(), as_of=NOW)

    def test_cli_read_only_hash_pinning_and_sanitized_failures(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'capture.json'; path.write_bytes(json.dumps([fixture()]).encode())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            args = ['--response',str(path),'--response-sha256',digest,'--data-config',str(Path(tmp)/'config.json'),
                    '--as-of',NOW.isoformat(),'--cutoff',NOW.date().isoformat()]
            config_path = Path(tmp)/'config.json'
            config_path.write_text(json.dumps({'data_directory': '.', 'leagues': ['E1'], 'max_age_days': 14}))
            with patch('modelfc.offline_identity_audit.history_bytes_from_config', return_value=sources()), \
                 redirect_stdout(StringIO()) as out:
                before = path.read_bytes()
                self.assertEqual(main(args), 0)
                self.assertEqual(json.loads(out.getvalue())['provider_requests'], 0)
                self.assertEqual(path.read_bytes(), before)
            for variant in ('hash', 'symlink', 'hardlink', 'missing'):
                bad = args.copy()
                if variant == 'hash': bad[3] = '0'*64
                else:
                    alias = Path(tmp)/variant
                    if variant == 'symlink': alias.symlink_to(path)
                    if variant == 'hardlink': alias.hardlink_to(path)
                    bad[1] = str(alias)
                with redirect_stdout(StringIO()) as out:
                    self.assertEqual(main(bad), 1)
                    self.assertEqual(json.loads(out.getvalue()), {'status':'IDENTITY_AUDIT_REJECTED'})

    def test_cli_config_is_bounded_regular_nofollow_no_reread(self):
        import os
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            response = root/'response.json'; response.write_text(json.dumps([fixture()]))
            config = root/'config.json'
            config.write_text(json.dumps({'data_directory': '.', 'leagues': ['E1'], 'max_age_days': 14}))
            args = ['--response',str(response),'--response-sha256',hashlib.sha256(response.read_bytes()).hexdigest(),
                    '--data-config',str(config),'--as-of',NOW.isoformat(),'--cutoff',NOW.date().isoformat()]
            for variant in ('fifo','symlink','hardlink','oversize','malformed','duplicate','missing','directory'):
                path = root/variant
                if variant == 'fifo': os.mkfifo(path)
                elif variant == 'symlink': path.symlink_to(config)
                elif variant == 'hardlink': path.hardlink_to(config)
                elif variant == 'oversize': path.write_bytes(b' '*16_385)
                elif variant == 'malformed': path.write_text('PRIVATE-MARKER')
                elif variant == 'duplicate': path.write_text('{"leagues":[],"leagues":["E1"]}')
                elif variant == 'directory': path.mkdir()
                bad = args.copy(); bad[5] = str(path)
                with patch('modelfc.offline_identity_audit.history_bytes_from_config', side_effect=AssertionError('history')), \
                     patch('modelfc.corner_prospective._RequestBudgetGuard.reserve', side_effect=AssertionError('budget')), \
                     patch('socket.socket', side_effect=AssertionError('HTTP')), redirect_stdout(StringIO()) as out:
                    self.assertEqual(main(bad), 1)
                    self.assertEqual(json.loads(out.getvalue()), {'status':'IDENTITY_AUDIT_REJECTED'})
            (root/'hardlink').unlink()
            # Validate once and pass that same object to the protected history reader.
            with patch('modelfc.corner_data.load_data_config', side_effect=AssertionError('unbounded reread')), \
                 patch('modelfc.offline_identity_audit.history_bytes_from_config', return_value=sources()) as reader, \
                 redirect_stdout(StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(reader.call_args.args[0].directory, root)
                self.assertEqual(reader.call_args.args[0].max_age_days, 14)
