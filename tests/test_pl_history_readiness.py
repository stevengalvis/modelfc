"""Synthetic integration evidence only; no genuine EPL acceptance."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.pl_history_readiness import assess, main
from tests.test_pl_calendar import synthetic, raw, sha, NOW

HEADER = 'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n'


class PLHistoryTests(unittest.TestCase):
    def setUp(self):
        self.contract, self.payload = synthetic()
        for match in self.payload['matches'][10:20]:
            match.update(utcDate='2026-10-16T14:00:00Z', status='FINISHED')
            match['score']['fullTime'] = {'home': 1, 'away': 0}
        self.payload['resultSet']['played'] = 10
        self.sources = []
        names = {b['source_team_id']: b['history_name'] for b in self.contract['team_bindings']}
        for year in range(2022, 2027):
            matches = (self.payload['matches'][10:20] if year == 2026 else
                       self.payload['matches'][:20] + self.payload['matches'][190:210])
            csv = HEADER
            for i, match in enumerate(matches):
                day = '16/10' if year == 2026 else f'{1+i//10:02d}/09'
                csv += f"E0,{day}/{year},{names[match['homeTeam']['id']]},{names[match['awayTeam']['id']]},1,0,H,5,3\n"
            data = csv.encode()
            self.sources.append((f'E0_{year%100:02d}{(year+1)%100:02d}.csv', data, sha(data), NOW))

    def options(self):
        bundle = raw({'version': 1, 'source': 'football-data.org', 'retrieved_at': NOW.isoformat(),
                      'payload': json.dumps(self.payload)})
        return bundle, dict(contract_raw=raw(self.contract), reviewed_contract_sha256=sha(raw(self.contract)),
            competition='E0', season_year=2026, selected_day=NOW.date(), as_of=NOW, bundle_sha256=sha(bundle))

    def report(self, **changes):
        bundle, options = self.options()
        return assess(bundle, **dict(sources=self.sources, enabled=True, **options, **changes))

    def replace_current(self, transform):
        name, data, _, retrieved = self.sources[-1]
        data = transform(data)
        self.sources[-1] = name, data, sha(data), retrieved

    def test_positive_day_pinned_identity_venues_no_network(self):
        with patch('socket.socket', side_effect=AssertionError('network')), patch('socket.getaddrinfo', side_effect=AssertionError('DNS')):
            report = self.report()
        self.assertEqual(report['state'], 'INPUTS_ELIGIBLE')
        self.assertEqual(len(report['fixtures']), 8)
        self.assertEqual(report['calendar_completeness'], 'MATCHED_BEFORE_DAY')
        self.assertTrue(all(f['corner_inputs_eligible'] for f in report['fixtures']))
        self.assertEqual(report['teams'][0]['source_team_id'], 1000)
        self.assertEqual(report['teams'][0]['provider'], 'football-data.org')
        self.assertGreaterEqual(report['teams'][0]['complete_corner_venues']['home'], 8)
        self.assertFalse(report['live_activation'])

    def test_opt_in_and_missing_calendar_history(self):
        self.assertEqual(assess(None)['state'], 'DISABLED')
        bundle, options = self.options()
        self.assertEqual(assess(None, enabled=True, **options)['calendar']['validity'], 'MISSING')
        self.assertEqual(assess(bundle, enabled=True, **options)['history_validity'], 'MISSING')

    def test_empty_day_never_masks_bad_history_or_stale_calendar(self):
        for match in self.payload['matches'][:8]:
            match['utcDate'] = '2026-10-18T14:00:00Z'
        self.assertEqual(self.report()['state'], 'EMPTY')
        self.sources.pop(1)
        result = self.report()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual(result['missing_season_sources'], ['E0_2324.csv'])
        bundle, options = self.options()
        options['as_of'] = NOW+timedelta(hours=7)
        result = assess(bundle, enabled=True, sources=self.sources, **options)
        self.assertEqual(result['calendar']['freshness'], 'STALE')
        self.assertEqual(result['calendar']['selected_day_state'], 'UNKNOWN')
        self.assertEqual(result['state'], 'BLOCKED')

    def test_unknown_stale_future_provenance_and_wrong_pin(self):
        original = list(self.sources)
        for retrieved, expected in [(None, 'UNKNOWN'), (NOW-timedelta(days=15), 'INVALID'), (NOW+timedelta(seconds=1), 'INVALID')]:
            name, data, pin, _ = original[-1]
            self.sources[-1] = name, data, pin, retrieved
            result = self.report()
            self.assertEqual(result['state'], 'BLOCKED')
            if expected == 'INVALID':
                self.assertEqual(result['history_validity'], 'INVALID_OR_UNSUPPORTED')
            else:
                self.assertEqual(result['history_freshness'], expected)
        self.sources = original
        name, data, pin, retrieved = self.sources[0]
        self.sources[0] = name, data, pin, None
        self.assertEqual(self.report()['history_provenance'], 'UNKNOWN')
        self.sources[0] = name, data, '0'*64, retrieved
        self.assertEqual(self.report()['history_validity'], 'INVALID_OR_UNSUPPORTED')

    def test_retrieval_precedes_result_date_or_exact_calendar_kickoff(self):
        name, data, pin, _ = self.sources[-1]
        for retrieved in (NOW-timedelta(days=2), NOW-timedelta(days=1),
                          NOW-timedelta(hours=20)):
            self.sources[-1] = name, data, pin, retrieved
            result = self.report()
            self.assertEqual(result['state'], 'BLOCKED')
            self.assertEqual(result['history_validity'], 'INVALID_OR_UNSUPPORTED')

    def test_stale_history_and_global_count_gate(self):
        bundle, options = self.options()
        options.update(as_of=NOW+timedelta(days=15), selected_day=(NOW+timedelta(days=15)).date())
        result = assess(bundle, enabled=True, sources=self.sources, **options)
        self.assertEqual(result['history_freshness'], 'STALE')
        self.assertEqual(result['state'], 'BLOCKED')
        # All five source seasons exist, but too few complete observations.
        for index, (name, data, _, retrieved) in enumerate(self.sources[:-1]):
            data = b'\n'.join(data.splitlines()[:3])+b'\n'
            self.sources[index] = name, data, sha(data), retrieved
        result = self.report()
        self.assertLess(result['complete_corner_observations'], 100)
        self.assertEqual(result['state'], 'BLOCKED')

    def test_finished_missing_score_conflict_missing_corners_and_extra(self):
        original = list(self.sources)
        for transform, diagnostic in [
            (lambda b: b.rsplit(b'\n', 2)[0]+b'\n', 'missing_finished'),
            (lambda b: b.replace(b',1,0,H,', b',2,0,H,', 1), 'score_conflicts'),
            (lambda b: b.replace(b',5,3', b',,3', 1), 'missing_corner_fixtures'),
            (lambda b: b.replace(b'16/10', b'15/10', 1), 'extra_history'),
        ]:
            self.sources = list(original)
            self.replace_current(transform)
            result = self.report()
            self.assertEqual(result['state'], 'BLOCKED')
            self.assertGreater(result['reconciliation'][diagnostic], 0)

    def test_no_alias_inference_promoted_team_and_venue_gap(self):
        # A different old label must not silently supply this team's history.
        for index, (name, data, _, retrieved) in enumerate(self.sources[:-1]):
            data = data.replace(b'Synthetic History 0,', b'Older Label 0,')
            self.sources[index] = name, data, sha(data), retrieved
        result = self.report()
        team = result['teams'][0]
        self.assertEqual(len(team['seasons_without_corner_history']), 4)
        self.assertFalse(team['home_eligible'])
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(result['fixtures'][0]['corner_inputs_eligible'])

    def test_duplicate_wrong_competition_filename_season_and_zero_corner(self):
        original = list(self.sources)
        for transform in [lambda b: b+b.splitlines(keepends=True)[1],
                          lambda b: b.replace(b'E0,', b'E1,'),
                          lambda b: b.replace(b'/2026,', b'/2025,')]:
            self.sources = list(original)
            self.replace_current(transform)
            self.assertEqual(self.report()['history_validity'], 'INVALID_OR_UNSUPPORTED')
        self.sources = original
        self.replace_current(lambda b: b.replace(b',5,3', b',0,0'))
        self.assertEqual(self.report()['state'], 'INPUTS_ELIGIBLE')

    def test_same_day_excluded_and_non_scheduled_fixture_ineligible(self):
        self.replace_current(lambda b: b+b'E0,17/10/2026,New Home,New Away,1,0,H,5,3\n')
        result = self.report()
        self.assertEqual(result['excluded_at_or_after_cutoff'], 1)
        self.assertEqual(result['complete_corner_observations'], 340)
        self.payload['matches'][0]['status'] = 'CANCELLED'
        result = self.report()
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(result['fixtures'][0]['corner_inputs_eligible'])

    def test_cli_readonly_and_unknown_provenance(self):
        bundle, options = self.options()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'bundle').write_bytes(bundle)
            (root/'contract').write_bytes(raw(self.contract))
            args = ['--enable-offline-audit', '--bundle', str(root/'bundle'), '--bundle-sha256', sha(bundle),
                '--contract', str(root/'contract'), '--reviewed-contract-sha256', options['reviewed_contract_sha256'],
                '--season-year', '2026', '--day', NOW.date().isoformat(), '--as-of', NOW.isoformat()]
            for name, data, pin, retrieved in self.sources:
                (root/name).write_bytes(data)
                args += ['--source', str(root/name), pin, retrieved.isoformat()]
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
            out = io.StringIO()
            with redirect_stdout(out), patch('socket.socket', side_effect=AssertionError('network')):
                self.assertEqual(main(args), 0)
            self.assertNotIn(directory, out.getvalue())
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()})
            missing_args = list(args)
            missing_args[-3] = str(root/'missing.csv')
            missing_output = io.StringIO()
            with redirect_stdout(missing_output):
                self.assertEqual(main(missing_args), 1)
            self.assertEqual(json.loads(missing_output.getvalue())['history_validity'], 'MISSING')
            args[-1] = 'UNKNOWN'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 1)
                self.assertEqual(main(args[1:]), 1)  # disabled by default

    def test_cli_no_arguments_reports_disabled_without_reading(self):
        output = io.StringIO()
        with redirect_stdout(output), patch('modelfc.pl_history_readiness.read_bounded', side_effect=AssertionError('read')):
            self.assertEqual(main([]), 1)
        self.assertEqual(json.loads(output.getvalue())['state'], 'DISABLED')

    def test_cli_enabled_requires_each_evidence_argument_before_reading(self):
        names = ('bundle', 'bundle-sha256', 'contract', 'reviewed-contract-sha256', 'season-year', 'day', 'as-of')
        for missing in (None, *names):
            with self.subTest(missing=missing):
                args = ['--enable-offline-audit']
                if missing is not None:
                    for name in names:
                        if name != missing:
                            args.extend(['--'+name, 'unused'])
                error = io.StringIO()
                with redirect_stderr(error), patch('modelfc.pl_history_readiness.read_bounded', side_effect=AssertionError('read')):
                    with self.assertRaises(SystemExit) as caught:
                        main(args)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(error.getvalue(), 'PL_ARGUMENTS_REJECTED\n')
