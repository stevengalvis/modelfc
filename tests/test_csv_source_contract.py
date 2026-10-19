"""ZEN-38 source contract checks; all HTTP responses are synthetic."""
from datetime import date, datetime, timezone
from email.message import Message
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.response import addinfourl

from modelfc import corner_refresh as refresh, refresh_result_evidence as evidence
from modelfc.corner_data import CornerDataConfig


URL = 'https://football-data.co.uk/mmz4281/2627/E1.csv'
LEGACY = 'https://www.football-data.co.uk/mmz4281/2627/E1.csv'
RAW = b'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,13/10/2026,A,B,1,0,H,4,3\n'


class SourceContractTests(unittest.TestCase):
    def test_e1_published_url_retains_original_bytes_and_http_provenance(self):
        headers = Message()
        headers['Date'] = 'Mon, 19 Oct 2026 06:00:00 GMT'
        response = addinfourl(BytesIO(RAW), headers, URL, 200)
        with TemporaryDirectory() as root:
            state = Path(root) / 'refresh'; state.mkdir()
            config = CornerDataConfig(Path(root), ('E1',), 14)
            with patch.object(refresh, 'urlopen', return_value=response) as request:
                row = refresh.refresh_league(config, 'E1', date(2026, 10, 19), state,
                                             retain_result_evidence=True)
            request.assert_called_once_with(URL, timeout=45)
            self.assertEqual(row['source'], URL)
            metadata, raw = evidence.load(state, row['result_evidence']['sha256'],
                                          as_of=datetime.now(timezone.utc))
            self.assertEqual(raw, RAW)
            self.assertEqual(metadata['file_sha256'], hashlib.sha256(RAW).hexdigest())
            self.assertEqual(metadata['url'], metadata['response_url'])
            self.assertEqual(metadata['headers'], {'Date': headers['Date']})
            self.assertLessEqual(metadata['requested_at_utc'], metadata['retrieved_at_utc'])

    def test_redirects_never_issue_second_get_or_mint_provenance(self):
        for code in (301, 302, 303, 307, 308):
            for destination in (LEGACY, 'https://football-data.co.uk/challenge',
                                'https://unrelated.invalid/E1.csv'):
                with self.subTest(code=code, destination=destination):
                    calls = []; metadata = {}
                    def response(handler, request):
                        calls.append((request.get_method(), request.full_url))
                        headers = Message(); headers['Location'] = destination
                        result = addinfourl(BytesIO(b''), headers, request.full_url, code)
                        result.msg = 'Redirect'
                        return result
                    with patch('urllib.request.HTTPSHandler.https_open', new=response), \
                         patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                        with self.assertRaisesRegex(HTTPError, 'one GET ceiling'):
                            refresh.download_csv(URL, metadata=metadata)
                    self.assertEqual(calls, [('GET', URL)])
                    self.assertEqual(metadata, {})

    def test_exact_source_pairs_and_provenance_fail_closed(self):
        metadata = dict(schema_version=1, url=URL, response_url=URL, http_status=200,
                        requested_at_utc='2026-10-19T06:00:00+00:00',
                        retrieved_at_utc='2026-10-19T06:00:01+00:00', headers={},
                        filename='E1_2627.csv', file_sha256=hashlib.sha256(RAW).hexdigest())
        as_of = datetime(2026, 10, 19, 7, tzinfo=timezone.utc)
        evidence.validate(metadata, RAW, as_of=as_of)
        # Retained legacy evidence remains readable without rewriting its URL or hash.
        evidence.validate({**metadata, 'url': LEGACY, 'response_url': LEGACY}, RAW, as_of=as_of)
        invalid = [
            {'url': LEGACY}, {'response_url': LEGACY}, {'http_status': 302},
            {'http_status': 304}, {'file_sha256': '0' * 64}, {'filename': 'E1_2526.csv'},
            {'requested_at_utc': '2026-10-19T06:00:02+00:00'},
            {'retrieved_at_utc': '2026-10-20T06:00:00+00:00'},
        ]
        for url in (URL.replace('https:', 'http:'), URL + '?download=1',
                    URL.replace('E1.csv', 'SP1.csv'), URL.replace('2627', '2526'),
                    'https://football-data.co.uk/challenge',
                    'https://football-data.co.uk.untrusted.invalid/mmz4281/2627/E1.csv'):
            invalid.append({'url': url, 'response_url': url})
        for change in invalid:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'INVALID_REFRESH_EVIDENCE'):
                evidence.validate({**metadata, **change}, RAW, as_of=as_of)
        with self.assertRaisesRegex(ValueError, 'INVALID_REFRESH_EVIDENCE'):
            evidence.validate(metadata, RAW + b'\n', as_of=as_of)
