"""Observed CLI usage, never a spending cap or proof of model provenance."""
import importlib.util
import json
from pathlib import Path

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
          'cache_write_input_tokens', 'reasoning_output_tokens')


def need(value):
    if not value:
        raise ValueError('REPAIR_USAGE_REJECTED')


def normalize(value):
    if value is None:
        return dict(state='UNKNOWN', **{key:None for key in (*FIELDS, 'total_tokens')})
    need(type(value) is dict and set(FIELDS[:3]) <= set(value) <= set(FIELDS))
    need(all(type(n) is int and 0 <= n < 2**63 for n in value.values()))
    # Cached input and reasoning output are detail counts, not additional tokens.
    return dict(state='REPORTED', **{key:value.get(key) for key in FIELDS},
                total_tokens=value['input_tokens'] + value['output_tokens'])


def validate_report(value):
    need(type(value) is dict and set(value) == {'state', 'total_tokens', *FIELDS})
    if value['state'] == 'UNKNOWN':
        need(value == normalize(None))
    else:
        need(value['state'] == 'REPORTED')
        need(type(value['total_tokens']) is int)
        raw = {key:value[key] for key in FIELDS if value[key] is not None}
        need(value == normalize(raw))
    return dict(value)


def from_capture(raw, returncode, timedout):
    """Missing/failed/unsupported captures have unknown usage; never infer zero.

    Proposal admission separately calls the strict capture parser. UNKNOWN here
    cannot turn a rejected or timed-out capture into an admissible model result.
    """
    path = Path(__file__).parents[1] / 'model-patch/capture.py'
    spec = importlib.util.spec_from_file_location('repair_usage_capture', path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    try:
        parser.extract(raw, returncode, timedout)
        return normalize(json.loads(raw.splitlines()[-1])['usage'])
    except (ValueError, TypeError, KeyError, UnicodeError):
        return normalize(None)


def aggregate(records):
    need(type(records) is list and len(records) <= 2)
    rows = [validate_report(row) for row in records]
    reported = sum(row['state'] == 'REPORTED' for row in rows)
    result = {key:sum(row[key] for row in rows) if rows and all(row[key] is not None for row in rows) else None
              for key in (*FIELDS, 'total_tokens')}
    return dict(result, attempts=len(rows), reported_attempts=reported,
                state='REPORTED' if rows and reported == len(rows) else 'PARTIAL' if reported else 'UNKNOWN',
                known_total_tokens=sum(row['total_tokens'] for row in rows if row['total_tokens'] is not None))
