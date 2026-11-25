"""Offline unit tests for ZEN53 Python fixture archive acquisition."""
import hashlib
import importlib.util
import io
from pathlib import Path
import unittest
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'docs/zen53/rotation-decoupling/packaging/prepare_python_fixture.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('zen53_prepare_python_fixture', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_module()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, size=-1):
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _row(payload: bytes, url: str = 'https://security.ubuntu.com/ubuntu/pool/main/g/glibc/libc6_2.39-0ubuntu8.9_amd64.deb'):
    return {
        'name': 'libc6',
        'url': url,
        'bytes': len(payload),
        'sha256': hashlib.sha256(payload).hexdigest(),
    }


def _http_error(url: str, code: int):
    return urllib.error.HTTPError(url, code, f'HTTP {code}', {}, None)


class PreparePythonFixtureDownloadTests(unittest.TestCase):
    def test_official_url_succeeds_without_hitting_snapshot(self):
        payload = b'valid-deb-bytes'
        row = _row(payload)
        calls = []
        sleeps = []

        def opener(url, timeout=60):
            calls.append((url, timeout))
            return _FakeResponse(payload)

        data = MOD.download_archive(row, opener=opener, sleep=sleeps.append)
        self.assertEqual(data, payload)
        self.assertEqual(calls, [(row['url'], 60)])
        self.assertEqual(sleeps, [])

    def test_404_on_official_url_falls_back_immediately_to_snapshot(self):
        payload = b'superseded-libc6-bytes'
        row = _row(payload)
        official, snapshot = MOD.candidate_urls(row)
        calls = []
        sleeps = []

        def opener(url, timeout=60):
            calls.append(url)
            if url == official:
                raise _http_error(url, 404)
            return _FakeResponse(payload)

        data = MOD.download_archive(row, opener=opener, sleep=sleeps.append)
        self.assertEqual(data, payload)
        self.assertEqual(calls, [official, snapshot])
        self.assertEqual(sleeps, [])

    def test_transient_503_on_snapshot_retries_with_bounded_backoff_and_succeeds(self):
        payload = b'retried-snapshot-bytes'
        row = _row(payload)
        official, snapshot = MOD.candidate_urls(row)
        calls = []
        sleeps = []
        snapshot_attempts = 0

        def opener(url, timeout=60):
            nonlocal snapshot_attempts
            calls.append(url)
            if url == official:
                raise _http_error(url, 404)
            snapshot_attempts += 1
            if snapshot_attempts < 3:
                raise _http_error(url, 503)
            return _FakeResponse(payload)

        data = MOD.download_archive(row, opener=opener, sleep=sleeps.append)
        self.assertEqual(data, payload)
        self.assertEqual(calls, [official, snapshot, snapshot, snapshot])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_transient_503_exhausting_retries_fails_bounded(self):
        row = _row(b'unreachable-bytes')
        official, snapshot = MOD.candidate_urls(row)
        calls = []
        sleeps = []

        def opener(url, timeout=60):
            calls.append(url)
            if url == official:
                raise _http_error(url, 404)
            raise _http_error(url, 503)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            MOD.download_archive(row, opener=opener, sleep=sleeps.append, max_attempts=3)
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(calls, [official, snapshot, snapshot, snapshot])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_checksum_or_size_mismatch_fails_closed_without_fallback(self):
        row = _row(b'expected-bytes')
        for bad_payload in (b'expected-byteX', b'short', b'expected-bytes-extra'):
            calls = []
            with self.subTest(bad_payload=bad_payload):
                def opener(url, timeout=60, payload=bad_payload):
                    calls.append(url)
                    return _FakeResponse(payload)

                with self.assertRaisesRegex(ValueError, '^ARCHIVE_HASH$'):
                    MOD.download_archive(row, opener=opener, sleep=lambda _: None)
                self.assertEqual(calls, [row['url']])


if __name__ == '__main__':
    unittest.main()
