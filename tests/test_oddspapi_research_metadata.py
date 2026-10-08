import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.oddspapi_research_metadata import filter_metadata, main
from modelfc.oddspapi_tournament_research import TournamentResearchError, analyze_batch
from tests.test_oddspapi_tournament_research import metadata, payload, NOW


class ResearchMetadataTests(unittest.TestCase):
    def encode(self, rows):
        return json.dumps(rows).encode()

    def test_retains_all_relevant_definitions_and_alternate_lines_unchanged(self):
        rows = metadata()
        alternate = copy.deepcopy(rows[5])
        alternate.update(marketId=99, handicap=6.5)
        rows.append(alternate)
        output, receipt = filter_metadata(self.encode(rows))
        self.assertEqual(json.loads(output), sorted(rows, key=lambda row: row['marketId']))
        self.assertEqual(receipt['filtered_entries'], 9)
        self.assertEqual(receipt['filtered_sha256'], hashlib.sha256(output).hexdigest())
        self.assertEqual(receipt['source_sha256'], hashlib.sha256(self.encode(rows)).hexdigest())

    def test_other_sports_removed_but_unsupported_football_and_player_props_retained(self):
        rows = metadata()
        for mid, sport, kind, prop in ((90, 1, 'totals', False),
                                      (91, 10, 'moneyline', False),
                                      (92, 10, 'totals', True)):
            row = copy.deepcopy(rows[0])
            row.update(marketId=mid, sportId=sport, marketType=kind, playerProp=prop)
            rows.append(row)
        output, receipt = filter_metadata(self.encode(rows))
        self.assertEqual([row['marketId'] for row in json.loads(output)], list(range(1, 9)) + [91, 92])
        self.assertEqual(receipt['family_entries']['UNSUPPORTED_FOOTBALL'], 2)

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
        self.assertEqual(output, reordered)
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
        self.assertEqual(json.loads(output), metadata())

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
