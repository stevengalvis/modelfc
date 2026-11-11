"""Pure fixed-fixture response validation; no execution or provenance authority.

The trusted caller supplies the expected request ID. A valid response does not
establish who produced it, authenticate a model run, or establish absence of tool
activity. Raw-byte and replacement hashes bind data only. This module never reads
files, executes replacement text, applies a patch, or contacts a provider.
"""
import hashlib
import json

BEFORE = b'def add(a, b):\n    return a - b\n'
AFTER = b'def add(a, b):\n    return a + b\n'
BASE_SHA256 = hashlib.sha256(BEFORE).hexdigest()
MAX_RESPONSE_BYTES = 4096
ERROR = 'MODEL_PATCH_RESPONSE_REJECTED'


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _hex64(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result)
        result[key] = value
    return result


def _constant(value):
    raise ValueError(ERROR)


def validate_response(raw, expected_request_id):
    """Return a fresh exact data binding; reject all other edits or response shapes."""
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE_BYTES)
    _need(_hex64(expected_request_id))
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        _need(type(value) is dict and set(value) == {
            'version', 'request_id', 'base_sha256', 'path', 'replacement'})
        _need(type(value['version']) is int and value['version'] == 1)
        _need(_hex64(value['request_id']) and value['request_id'] == expected_request_id)
        _need(type(value['base_sha256']) is str and value['base_sha256'] == BASE_SHA256)
        _need(type(value['path']) is str and value['path'] == 'calc.py')
        _need(type(value['replacement']) is str and value['replacement'] == AFTER.decode('ascii'))
    except (ValueError, UnicodeError, RecursionError):
        # Do not propagate parser excerpts containing untrusted response text.
        raise ValueError(ERROR) from None
    return dict(version=1, request_id=expected_request_id, base_sha256=BASE_SHA256,
                path='calc.py', replacement=value['replacement'],
                response_sha256=hashlib.sha256(raw).hexdigest(),
                replacement_sha256=hashlib.sha256(value['replacement'].encode('ascii')).hexdigest())
