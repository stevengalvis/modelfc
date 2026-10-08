import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.oddspapi_research_metadata import filter_metadata, main
from modelfc.oddspapi_tournament_research import TournamentResearchError, analyze_batch, _metadata_index
from tests.test_oddspapi_tournament_research import metadata, payload, market, NOW


class ResearchMetadataTests(unittest.TestCase):
    def encode(self, rows):
        return json.dumps(rows).encode()

    def test_retains_all_relevant_definitions_and_alternate_lines_unchanged(self):
        rows = metadata()
        alternate = copy.deepcopy(rows[5])
        alternate.update(marketId=99, handicap=6.5)
        rows.append(alternate)
        output, receipt = filter_metadata(self.encode(rows))
        self.assertEqual(json.loads(output)["markets"], sorted(rows, key=lambda row: row['marketId']))
        self.assertEqual(receipt['filtered_entries'], 9)
        self.assertEqual(receipt['filtered_sha256'], hashlib.sha256(output).hexdigest())
        self.assertEqual(receipt['source_sha256'], hashlib.sha256(self.encode(rows)).hexdigest())

    def test_non_target_sports_unsupported_families_and_player_props_excluded(self):
        rows = metadata()
        for mid, sport, kind, prop in ((90, 1, 'totals', False),
                                      (91, 10, 'moneyline', False),
                                      (92, 10, 'totals', True)):
            row = copy.deepcopy(rows[0])
            row.update(marketId=mid, sportId=sport, marketType=kind, playerProp=prop)
            rows.append(row)
        output, receipt = filter_metadata(self.encode(rows))
        self.assertEqual([row['marketId'] for row in json.loads(output)['markets']], list(range(1, 9)))
        self.assertEqual(receipt['excluded_entries'], 3)
        self.assertEqual(json.loads(output)['excluded_ids'], {
            'EXCLUDED_BY_ALLOWLIST': [90, 92], 'UNSUPPORTED_FAMILY': [91]})

    def test_inventory_identical_for_complete_and_missing_outcome_mappings(self):
        rows = metadata()
        output, _ = filter_metadata(self.encode(rows))
        for data in (payload(),):
            self.assertEqual(analyze_batch(data, rows, observed_at=NOW),
                             analyze_batch(data, json.loads(output), observed_at=NOW))
        data = payload()
        book = data[0]['bookmakerOdds']['draftkings']
        book['markets']['999'] = copy.deepcopy(book['markets']['1'])
        book['markets']['1']['outcomes']['999'] = {'players': {}}
        result = analyze_batch(data, json.loads(output), observed_at=NOW)
        self.assertEqual(result, analyze_batch(data, rows, observed_at=NOW))
        fixture = next(row for row in result['competitions'] if row['tournament_id'] == 18)['fixtures'][0]
        book_result = fixture['bookmakers']['draftkings']
        self.assertEqual(book_result['unsupported_metadata_markets'], 1)
        self.assertEqual(book_result['unsupported_metadata_outcomes'], 1)

    def test_deterministic_output_order_and_size_bounds(self):
        rows = metadata()
        output, _ = filter_metadata(self.encode(rows))
        reordered, _ = filter_metadata(self.encode(list(reversed(rows))))
        self.assertEqual(output, filter_metadata(self.encode(rows))[0])
        self.assertEqual(json.loads(output)['markets'], json.loads(reordered)['markets'])
        self.assertEqual(json.loads(output)['excluded_ids'], json.loads(reordered)['excluded_ids'])
        with patch('modelfc.oddspapi_research_metadata.MAX_METADATA_BYTES', len(output) - 1):
            with self.assertRaisesRegex(TournamentResearchError, 'LIMIT_EXCEEDED'):
                filter_metadata(self.encode(rows))
        with patch('modelfc.oddspapi_research_metadata.MAX_METADATA_ROWS', 1):
            with self.assertRaises(TournamentResearchError):
                filter_metadata(self.encode(rows))

    def test_malformed_duplicate_and_missing_mappings_rejected_even_when_removed(self):
        cases = [b'bad', b'[]', b'[{"marketId":1,"marketId":2}]', b'[NaN]']
        rows = metadata()
        cases.append(self.encode(rows + [rows[0]]))
        for key, value in (('outcomes', None), ('outcomes', []), ('sportId', None),
                           ('marketId', True), ('marketType', [])):
            changed = copy.deepcopy(rows)
            changed[0][key] = value
            cases.append(self.encode(changed))
        changed = copy.deepcopy(rows)
        changed[0]['outcomes'].append(changed[0]['outcomes'][0])
        cases.append(self.encode(changed))
        changed = copy.deepcopy(rows)
        changed[0]['sportId'] = 1
        changed[0]['outcomes'] = None
        cases.append(self.encode(changed))
        for raw in cases:
            with self.subTest(raw=raw[:100]), self.assertRaises(TournamentResearchError):
                filter_metadata(raw)

    def test_oversized_all_sports_row_population_projects_within_loader_limits(self):
        rows = metadata()
        template = dict(rows[0], sportId=1)
        rows.extend(dict(template, marketId=identity) for identity in range(100, 33207))
        self.assertEqual(len(rows), 33115)
        output, receipt = filter_metadata(self.encode(rows))
        self.assertEqual(receipt['source_entries'], 33115)
        self.assertEqual(receipt['filtered_entries'], 8)
        self.assertEqual(json.loads(output)["markets"], metadata())

    def test_btts_104_uses_verified_type_and_yes_no_mapping(self):
        rows = metadata()
        btts = dict(rows[4], marketId=104, marketType='bothteamsscore', marketName='Localized name')
        rows.append(btts)
        output, _ = filter_metadata(self.encode(rows))
        self.assertEqual(json.loads(output)['markets'][-1], btts)
        dictionary, _ = _metadata_index(json.loads(output))
        self.assertEqual(dictionary['104'][1], 'BTTS')
        for names in (('Over', 'Under'), ('Yes', 'Yes'), ('Yes',)):
            broken = copy.deepcopy(btts)
            broken['outcomes'] = [{'outcomeId': i + 1, 'outcomeName': name} for i, name in enumerate(names)]
            with self.subTest(names=names), self.assertRaises(TournamentResearchError):
                filter_metadata(self.encode([broken]))

    def test_null_unknown_and_missing_period_excluded_without_normalizing(self):
        rows = metadata()
        for mid, period in ((101, None), (102, 'extra-time'), (103, '')):
            rows.append(dict(rows[0], marketId=mid, period=period))
        missing = dict(rows[0], marketId=105)
        del missing['period']
        rows.append(missing)
        rows.append(dict(rows[1], marketId=106, period='p1'))
        output, _ = filter_metadata(self.encode(rows))
        artifact = json.loads(output)
        self.assertEqual(artifact['markets'], metadata())
        self.assertEqual(artifact['excluded_ids']['EXCLUDED_BY_ALLOWLIST'], [101, 102, 103, 105, 106])
        self.assertIsNone(rows[8]['period'])

    def test_excluded_missing_and_unsupported_are_distinct_per_book(self):
        rows = metadata()
        rows.extend([dict(rows[0], marketId=101, period=None),
                     dict(rows[0], marketId=102, marketType='moneyline')])
        output, _ = filter_metadata(self.encode(rows))
        value = payload()
        value[0]['bookmakerOdds']['draftkings']['markets'] = {
            '101': market(101), '102': market(102), '999': market(999)}
        result = analyze_batch(value, json.loads(output), observed_at=NOW)
        books = next(row for row in result['competitions'] if row['tournament_id'] == 18)['fixtures'][0]['bookmakers']
        self.assertEqual(books['draftkings']['metadata_diagnostics'], [
            {'market_id': '101', 'status': 'EXCLUDED_BY_ALLOWLIST'},
            {'market_id': '102', 'status': 'UNSUPPORTED_FAMILY'},
            {'market_id': '999', 'status': 'MISSING_METADATA'}])
        self.assertEqual(books['draftkings']['unsupported_metadata_markets'], 1)
        self.assertEqual(books['fanduel']['metadata_diagnostics'], [])
        self.assertEqual(books['fanduel']['families']['BTTS']['status'], 'AVAILABLE')
        del value[0]['bookmakerOdds']['draftkings']['markets']['999']
        result = analyze_batch(value, json.loads(output), observed_at=NOW)
        book = next(row for row in result['competitions'] if row['tournament_id'] == 18)['fixtures'][0]['bookmakers']['draftkings']
        self.assertTrue(all(family['status'] == 'EXCLUDED_BY_ALLOWLIST' for family in book['families'].values()))

    def test_btts_104_inventory_preserves_present_incomplete_inactive_and_stale(self):
        btts = dict(metadata()[4], marketId=104, marketType='bothteamsscore')
        output, _ = filter_metadata(self.encode([btts]))
        for status, flags, empty in (
                ('AVAILABLE', {}, False), ('INCOMPLETE', {}, True),
                ('INACTIVE', {'marketActive': False}, True),
                ('STALE', {'staleOdds': True}, True)):
            value = payload()
            offer = market(5)
            offer.update(flags)
            if empty:
                offer['outcomes'] = {}
            value[0]['bookmakerOdds']['draftkings']['markets'] = {'104': offer}
            value[0]['bookmakerOdds']['fanduel']['markets'] = {}
            result = analyze_batch(value, json.loads(output), observed_at=NOW)
            books = next(row for row in result['competitions'] if row['tournament_id'] == 18)['fixtures'][0]['bookmakers']
            with self.subTest(status=status):
                self.assertEqual(books['draftkings']['families']['BTTS']['status'], status)
                self.assertEqual(books['draftkings']['families']['BTTS']['market_count'], 1)
                self.assertEqual(books['fanduel']['families']['BTTS']['status'], 'MISSING')

    def test_filtered_manifest_rejects_collisions_invalid_counts_and_unscoped_rows(self):
        output, _ = filter_metadata(self.encode(metadata()))
        for change in ('collision', 'count', 'unsupported', 'unknown_field', 'bad_id'):
            artifact = json.loads(output)
            if change == 'collision':
                artifact['excluded_ids']['UNSUPPORTED_FAMILY'] = [1]
                artifact['source_entries'] += 1
            elif change == 'count':
                artifact['source_entries'] += 1
            elif change == 'unsupported':
                artifact['markets'][0]['marketType'] = 'moneyline'
            elif change == 'unknown_field':
                artifact['anything'] = True
            else:
                artifact['excluded_ids']['UNSUPPORTED_FAMILY'] = [True]
                artifact['source_entries'] += 1
            with self.subTest(change=change), self.assertRaises(TournamentResearchError):
                _metadata_index(artifact)

    def test_cli_pins_source_writes_private_exclusive_artifacts_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source.json', root / 'filtered.json'
            raw = self.encode(metadata())
            source.write_bytes(raw)
            args = ['filter', str(source), str(output), '--source-sha256', hashlib.sha256(raw).hexdigest()]
            with patch('sys.argv', args), patch('builtins.print'):
                self.assertEqual(main(), 0)
                before = output.read_bytes()
                self.assertEqual(main(), 1)
                self.assertEqual(output.read_bytes(), before)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.with_name('filtered.json.provenance.json').stat().st_mode & 0o777, 0o600)
            with patch('sys.argv', args[:-1] + ['0' * 64]), patch('builtins.print'):
                self.assertEqual(main(), 1)
            link = root / 'link.json'
            link.symlink_to(source)
            with patch('sys.argv', ['filter', str(link), str(root / 'new.json'), *args[-2:]]), patch('builtins.print'):
                self.assertEqual(main(), 1)
