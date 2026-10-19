"""Real disposable Caddy ingress, synthetic upstream only; no production traffic."""
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode
from datetime import datetime, timezone

from ops.vps.patch_btts_ingress import OLD, EXPRESSION, patch_config

PATH = '/api/v1/research/btts/performance'
BLOCK = '''    @btts_performance {
        path /api/v1/research/btts/performance
        method GET
        expression `OLD`
    }
'''.replace('OLD', OLD)


def patched(raw):
    return patch_config(raw, hashlib.sha256(raw).hexdigest())


class PatchTests(unittest.TestCase):
    def test_only_expression_changes_in_unknown_surrounding_config(self):
        for newline in ('\n', '\r\n'):
            original = ('# preserve other routes\nexample.test {\n' + BLOCK +
                        '    handle /unrelated {\n        respond "unchanged"\n    }\n}\n').replace('\n', newline).encode()
            candidate = patched(original)
            self.assertEqual(candidate, original.replace(OLD.encode(), EXPRESSION.read_bytes().strip()))
            with self.assertRaises(ValueError):
                patched(candidate)

    def test_drift_duplicate_and_wrong_pin_fail_closed(self):
        raw = BLOCK.encode()
        with self.assertRaises(ValueError):
            patch_config(raw, '0' * 64)
        for changed in (raw + raw, raw.replace(b'method GET', b'method POST'),
                        raw.replace(b'performance\n', b'performance/*\n'),
                        raw.replace(b'competition=E1', b'competition=E0'),
                        raw.replace(b'method GET', b'method GET\n        query x=y')):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                patched(changed)


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = os.environ.get('MODELFC_CADDY_BIN')
        if not binary:
            raise unittest.SkipTest('real Caddy gateway exercised by dedicated ingress CI job')
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.requests = []

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(self.path)
                query = parse_qs(self.path.partition('?')[2], strict_parsing=True)
                status = 200
                if 'as_of' in query:
                    try:
                        when = datetime.fromisoformat(query['as_of'][0].replace('Z', '+00:00'))
                        if when.tzinfo is None or when > datetime.now(timezone.utc):
                            raise ValueError
                    except ValueError:
                        status = 422
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(query).encode())

            def log_message(self, *args):
                pass

        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.addClassCleanup(cls.upstream.server_close)
        thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(cls.upstream.shutdown)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        # Adapt and run the actual patched expression, with unchanged GET/path,
        # no-store, reverse_proxy and fallback semantics. Only ports are disposable.
        config = ('{\n admin off\n auto_https off\n}\nhttp://127.0.0.1:' + str(cls.port) + ' {\n' + BLOCK +
                  '    handle @btts_performance {\n        header Cache-Control "no-store"\n'
                  '        reverse_proxy 127.0.0.1:' + str(cls.upstream.server_port) + '\n    }\n'
                  '    handle /unrelated {\n        respond "unchanged" 200\n    }\n'
                  '    handle {\n        respond 404\n    }\n}\n').encode()
        config_path = root / 'Caddyfile'
        config_path.write_bytes(patched(config))
        env = {**os.environ, 'XDG_DATA_HOME': str(root / 'data'), 'XDG_CONFIG_HOME': str(root / 'config')}
        subprocess.run([binary, 'validate', '--config', str(config_path), '--adapter', 'caddyfile'],
                       env=env, capture_output=True, check=True, timeout=15)
        log = (root / 'caddy.log').open('w+')
        cls.addClassCleanup(log.close)
        cls.process = subprocess.Popen([binary, 'run', '--config', str(config_path), '--adapter', 'caddyfile'],
                                       env=env, stdout=log, stderr=log)
        def stop():
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill(); cls.process.wait(timeout=5)
        cls.addClassCleanup(stop)
        for _ in range(100):
            try:
                if cls.request('/unrelated')[0] == 200:
                    break
            except OSError:
                pass
            if cls.process.poll() is not None:
                log.seek(0)
                raise AssertionError(log.read())
            time.sleep(.05)
        else:
            raise AssertionError('Caddy did not become ready')

    @classmethod
    def request(cls, path, method='GET'):
        connection = HTTPConnection('127.0.0.1', cls.port, timeout=3)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_supported_scope_time_encoding_and_both_orders(self):
        times = ['2026-10-19T12:00:00Z', '2026-10-19T12:00:00.000Z',
                 '2026-10-19T12:00:00.123456Z', '2026-10-19T13:00:00+01:00',
                 '2026-10-19T07:00:00-05:00']
        queries = ['', 'competition=E1', 'competition=E0', 'competition=ALL']
        for stamp in times:
            # URLSearchParams encoding (the real TS client), raw colon/Z form,
            # lowercase percent escapes, standalone cutoff and either key order.
            encoded = urlencode({'as_of': stamp})
            variants = {encoded, encoded.replace('%3A', '%3a').replace('%2B', '%2b'),
                        encoded.replace('%3A', ':')}
            for value in variants:
                queries.append(value)
                for league in ('E1', 'E0', 'ALL'):
                    queries.extend([f'competition={league}&{value}', f'{value}&competition={league}'])
        for query in queries:
            with self.subTest(query=query):
                before = len(self.requests)
                path = PATH + ('?' + query if query else '')
                status, headers, body = self.request(path)
                self.assertEqual(status, 200, body)
                self.assertEqual(headers.get('Cache-Control'), 'no-store')
                self.assertEqual(json.loads(body), parse_qs(query))
                self.assertEqual(self.requests[before:], [path])

    def test_unknown_duplicate_malformed_and_mutation_never_reach_upstream(self):
        invalid = ['competition=E2', 'competition=', 'competition=e1', 'competition=ALLx',
                   'Competition=E1', 'x=1', 'competition=E1&x=1', 'x=1&competition=E1',
                   'competition=E1&competition=E0', 'competition=E1&competition=E1',
                   'competition=E1&', '&competition=E1', 'competition=E1&&as_of=2026-10-19T12:00:00Z',
                   'as_of=', 'as_of=2026-10-19', 'as_of=2026-10-19T12:00:00',
                   'as_of=2026-10-19T12:00:00+00:00', 'as_of=2026-10-19T12:00:00.1234567Z',
                   'as_of=2026-10-19T12:00:00Z&as_of=2026-10-19T12:00:00Z',
                   'competition=%45%31', '%63ompetition=E1', 'competition=E1%26x=1',
                   'as_of=2026-10-19T12%253A00%253A00Z', 'as_of=2026-10-19T12%3A00%3A00Z%00',
                   'as_of=2026-10-19T12%3A00%3A00Z%0A', 'as_of=' + '0' * 4096,
                   'as_of=2026-10-19T12:00:00Z;competition=E0']
        before = len(self.requests)
        for query in invalid:
            with self.subTest(query=query):
                self.assertIn(self.request(PATH + '?' + query)[0], (400, 404))
        for method in ('HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            with self.subTest(method=method):
                self.assertEqual(self.request(PATH + '?competition=ALL', method)[0], 404)
        for suffix in ('/', '/settle', '-extra'):
            self.assertEqual(self.request(PATH + suffix + '?competition=ALL')[0], 404)
        self.assertEqual(len(self.requests), before)
        self.assertEqual(self.request('/unrelated')[2], b'unchanged')

    def test_date_semantics_reach_backend_and_retain_no_store(self):
        for stamp in ('2026-02-30T12:00:00Z', '2026-10-19T25:00:00Z', '2099-01-01T00:00:00Z'):
            before = len(self.requests)
            status, headers, _ = self.request(PATH + '?' + urlencode({'competition': 'E1', 'as_of': stamp}))
            self.assertEqual(status, 422)
            self.assertEqual(headers.get('Cache-Control'), 'no-store')
            self.assertEqual(len(self.requests), before + 1)


if __name__ == '__main__':
    unittest.main()
