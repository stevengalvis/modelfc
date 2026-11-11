"""Offline validation only. No production issuer or claim store is configured."""
import json
import re

NONCE = re.compile(r'[0-9a-f]{32}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
PROFILE = 'zen53-python-diagnostic-v1'
KEYS = frozenset(('schema', 'profile', 'nonce', 'attempt_id',
                  'manifest_sha256', 'manifest_seal_sha256',
                  'tool_identity', 'stage_identity', 'manifest_identity',
                  'issued_at', 'expires_at', 'owner_approval_ref'))
MEASURED = frozenset(('nonce', 'manifest_sha256', 'manifest_seal_sha256',
                      'tool_identity', 'stage_identity', 'manifest_identity'))

class BindingRejected(ValueError):
    pass

def need(ok, code):
    if not ok:
        raise BindingRejected(code)

def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True) + '\n').encode('ascii')

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'BINDING_DUPLICATE_KEY')
        result[key] = value
    return result

def identity(value):
    return type(value) is list and len(value) == 2 and all(
        type(n) is int and n > 0 for n in value)

def decode(raw, now):
    """Parse protected issuer bytes; this alone does not prove the stage."""
    need(type(raw) is bytes and len(raw) <= 4096, 'BINDING_BOUND')
    try:
        item = json.loads(raw, object_pairs_hook=unique_pairs)
    except BindingRejected:
        raise
    except (ValueError, UnicodeError):
        raise BindingRejected('BINDING_JSON') from None
    need(type(item) is dict and set(item) == KEYS and canonical(item) == raw and
         item['schema'] == 'zen53-run-approval-v1' and
         item['profile'] == PROFILE, 'BINDING_SCHEMA')
    need(type(item['nonce']) is str and NONCE.fullmatch(item['nonce']) and
         type(item['attempt_id']) is str and NONCE.fullmatch(item['attempt_id']) and
         item['nonce'] != item['attempt_id'] and
         all(type(item[k]) is str and HASH.fullmatch(item[k]) for k in
             ('manifest_sha256', 'manifest_seal_sha256')) and
         all(identity(item[k]) for k in
             ('tool_identity', 'stage_identity', 'manifest_identity')),
         'BINDING_FIELDS')
    need(type(now) is int and now >= 0 and
         type(item['issued_at']) is int and item['issued_at'] >= 0 and
         type(item['expires_at']) is int and
         item['issued_at'] <= now < item['expires_at'] and
         0 < item['expires_at'] - item['issued_at'] <= 3600,
         'BINDING_EXPIRED')
    need(type(item['owner_approval_ref']) is str and
         1 <= len(item['owner_approval_ref']) <= 256 and
         all(0x21 <= ord(ch) <= 0x7e for ch in item['owner_approval_ref']),
         'BINDING_REFERENCE')
    return item

def validate(raw, measured, now):
    item = decode(raw, now)
    need(type(measured) is dict and set(measured) == MEASURED,
         'BINDING_MEASUREMENT')
    for key in MEASURED:
        need(item[key] == measured[key], 'BINDING_MISMATCH')
    return item

def authorize(issuer, measured, now):
    """Claim once before spawn; no production issuer adapter is supplied."""
    need(issuer is not None, 'BINDING_ISSUER_UNCONFIGURED')
    item = validate(issuer.read_current(), measured, now)
    need(issuer.claim_once(item['nonce'], item['attempt_id']) is True,
         'BINDING_CLAIM_UNCONFIRMED')
    return item
