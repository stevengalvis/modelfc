"""Untrusted response fixtures only: no model, code execution or file mutation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('model_patch_bridge', ROOT / 'docs/zen53/model-patch/bridge.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class ModelPatchResponse(unittest.TestCase):
    request_id = 'a' * 64

    def value(self):
        return dict(version=1, request_id=self.request_id, base_sha256=bridge.BASE_SHA256,
                    path='calc.py', replacement=bridge.AFTER.decode('ascii'))

    def raw(self, value=None):
        return json.dumps(self.value() if value is None else value).encode('utf-8')

    def reject(self, raw, request_id=None):
        with self.assertRaises(ValueError) as caught:
            bridge.validate_response(raw, self.request_id if request_id is None else request_id)
        self.assertEqual(str(caught.exception), bridge.ERROR)
        self.assertIsNone(caught.exception.__cause__)
        return caught.exception

    def test_fixed_bytes_match_existing_fixture_without_importing_payload(self):
        spec = importlib.util.spec_from_file_location('model_patch_fixture_store',
                    ROOT / 'docs/zen53/workspace-acceptance/workspace_store.py')
        store = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(store)
        self.assertEqual(bridge.BEFORE, store.WS_BEFORE)
        self.assertEqual(bridge.AFTER, store.WS_AFTER)

    def test_exact_response_returns_bound_data_only(self):
        raw = self.raw()
        result = bridge.validate_response(raw, self.request_id)
        self.assertEqual(result, self.value() | {
            'response_sha256': hashlib.sha256(raw).hexdigest(),
            'replacement_sha256': hashlib.sha256(bridge.AFTER).hexdigest()})
        result['replacement'] = 'mutated result'
        self.assertEqual(bridge.validate_response(raw, self.request_id)['replacement'], bridge.AFTER.decode())

    def test_whitespace_and_escaped_encoding_preserve_raw_byte_binding(self):
        raw = self.raw()
        escaped = raw.replace(b'calc.py', b'calc\\u002epy') + b' \n'
        first, second = [bridge.validate_response(value, self.request_id) for value in (raw, escaped)]
        self.assertNotEqual(first.pop('response_sha256'), second.pop('response_sha256'))
        self.assertEqual(first, second)

    def test_exact_bound_and_oversize_reject_before_parse(self):
        raw = self.raw()
        bound = raw + b' ' * (bridge.MAX_RESPONSE_BYTES - len(raw))
        self.assertEqual(bridge.validate_response(bound, self.request_id)['request_id'], self.request_id)
        for value in (bound + b' ', b'', raw.decode(), bytearray(raw), None):
            with self.subTest(kind=type(value)):
                self.reject(value)

    def test_duplicate_unknown_missing_and_nonobject_fields_rejected(self):
        raw = self.raw()
        for value in (raw[:-1] + b',"path":"calc.py"}',
                      raw[:-1] + b',"p\\u0061th":"calc.py"}',
                      self.raw(self.value() | {'command': 'secret-command'}),
                      self.raw({k: v for k, v in self.value().items() if k != 'replacement'}),
                      b'[]', b'null', b'false', b'1', b'"text"'):
            with self.subTest(value=value):
                self.reject(value)

    def test_version_paths_code_and_binding_mutations_rejected(self):
        changes = [('version', v) for v in (True, 1.0, 2, '1', None)]
        changes += [('path', p) for p in ('../calc.py', '/calc.py', './calc.py',
                    'calc.py/other', 'calc.py\x00', 'cаlc.py', ['calc.py'])]
        changes += [('replacement', v) for v in (bridge.BEFORE.decode(),
                     bridge.AFTER.decode() + '\nimport os\nos.system("SECRET")',
                     '$(SECRET)', '```python\n' + bridge.AFTER.decode() + '```',
                     bridge.AFTER.decode().replace('+', '＋'), None, {'command': 'SECRET'})]
        changes += [('base_sha256', v) for v in ('0'*64, True, bridge.BASE_SHA256.upper())]
        changes += [('request_id', v) for v in ('b'*64, 'A'*64, 'a'*63, True, None)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.reject(self.raw(self.value() | {key: value}))

    def test_trusted_request_binding_is_validated_but_zero_is_valid(self):
        for value in ('b'*64, 'A'*64, 'a'*63, True, 0, b'a'*64, 'g'*64):
            with self.subTest(value=value):
                self.reject(self.raw(), value)
        raw = self.raw(self.value() | {'request_id': '0'*64})
        self.assertEqual(bridge.validate_response(raw, '0'*64)['request_id'], '0'*64)
        with self.assertRaisesRegex(ValueError, '^' + bridge.ERROR + '$'):
            bridge.validate_response(self.raw(), None)

    def test_malformed_trailing_nonfinite_utf8_and_deep_input_have_fixed_error(self):
        for raw in (self.raw() + b'{}', b'SECRET', b'\xff', b'\xef\xbb\xbf' + self.raw(),
                    self.raw().replace(b'"version": 1', b'"version": NaN'),
                    self.raw().replace(b'"version": 1', b'"version": Infinity'),
                    b'['*1500 + b']'*1500, b'{"replacement":"\\ud800"}'):
            with self.subTest(size=len(raw)):
                self.reject(raw)


if __name__ == '__main__':
    unittest.main()
