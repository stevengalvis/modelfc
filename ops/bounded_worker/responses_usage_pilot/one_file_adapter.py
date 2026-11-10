"""Offline, single-scope preparation only. No provider, loader, exec or runtime.

A structural candidate check is not execution authorization or a test pass. The
prepared request must NOT be handed to the historical synthetic transport. A
reviewed readiness/transport/executor/cleanup controller is still required.
"""
import ast
from dataclasses import dataclass
import difflib

from .canonical_admission import canonical, digest
from .one_file_fixtures.trusted_harness import validate_function

BASE = '1112043f391d0a263c0f66688a3ddebd606bafb8'
SOURCE_PATH = 'src/modelfc/providers/_csv_values.py'
SOURCE_HASH = 'b3123b53f48e764511f1c73b93e620a287e4f4860309c3cdb5d93432809bc688'
FIXTURES = 'ops/bounded_worker/responses_usage_pilot/one_file_fixtures/'
TEST_HASH = '9b3fcfa5e2be17a5f417c176a02ca5505a3c390434e390cf136fe02dbe165f02'
HARNESS_HASH = '75a73a6fdf8d122d9d8d7573fede004db77dd12924ac75faea5c00f5c561332a'
TEST_NAMES = tuple('test_' + name for name in (
    'plain', 'trim', 'unicode', 'internal_space', 'empty', 'blank', 'none', 'missing', 'field'))
INSTRUCTIONS = (
    'Refactor only required_text in src/modelfc/providers/_csv_values.py to strip '
    'plain CSV str/None values once. Preserve the existing signature, missing-key KeyError, '
    'None/blank ValueError message, and all return values. This is a '
    'behavior-preserving cleanup, not a bug fix. Return the complete UTF-8 source '
    'file only, without Markdown. Do not change any other byte outside the function.'
)


@dataclass(frozen=True)
class Prepared:
    manifest_identity: str
    wire: bytes
    wire_hash: str
    package: tuple[tuple[str, bytes], ...]


def prepare(manifest, source, tests, harness):
    """Verify reviewed identities and build bytes without admission or side effects.

    Deliberately one call only: failure-feedback/retry transport is not implemented.
    Bytes/token is a conservative input ceiling, not billing or tokenizer evidence.
    """
    manifest.validate(source, tests, harness)
    expected = (BASE, SOURCE_PATH, 'required_text', SOURCE_HASH,
                FIXTURES + 'trusted_tests.py', TEST_HASH,
                FIXTURES + 'trusted_harness.py', HARNESS_HASH, TEST_NAMES, INSTRUCTIONS, 1)
    actual = (manifest.base_sha, manifest.source_path, manifest.function, manifest.source_hash,
              manifest.test_path, manifest.test_hash, manifest.harness_path,
              manifest.harness_hash, manifest.test_names, manifest.instructions, manifest.max_calls)
    if actual != expected:
        raise ValueError('unreviewed_task_identity')
    wire = canonical({
        'model': manifest.model, 'reasoning': {'effort': manifest.reasoning},
        'service_tier': manifest.service_tier, 'store': False, 'stream': False,
        'tools': [], 'tool_choice': 'none', 'truncation': 'disabled',
        'max_output_tokens': manifest.max_output_tokens,
        'input': manifest.instructions + '\n\n' + source.decode('utf-8'),
    }).encode('utf-8')
    if len(wire) > min(manifest.max_request_bytes, manifest.max_input_tokens):
        raise ValueError('request_bound')
    return Prepared(manifest.identity, wire, digest(wire), (
        (manifest.source_path, source), (manifest.test_path, tests),
        (manifest.harness_path, harness)))


def bind_first_reservation(manifest, prepared, reservation, source, tests, harness):
    """Pure consistency check, NOT proof of a durable reservation or readiness.

    Future controller must obtain reservation directly from CanonicalAdmission
    after exact readiness, before transmission, and never resend it on restart.
    No callback or method here transmits or authorizes execution.
    """
    if prepared != prepare(manifest, source, tests, harness):
        raise ValueError('prepared_bytes_changed')
    expected = {
        'number': 1,
        'request_hash': digest(canonical([manifest.identity, 1, []]).encode()),
        'sandbox_name': 'responses-task-' + manifest.identity + '-1',
    }
    if type(reservation) is dict and reservation.get('number') == 2:
        from .followup_allowance import reservation_plan
        expected = reservation_plan(manifest, reservation.get('approval_hash'))
    if (type(reservation) is not dict or reservation != expected
            or type(reservation.get('number')) is not int):
        raise ValueError('reservation_identity')
    return prepared


def _function(source):
    try:
        tree = ast.parse(source.decode('utf-8'))
    except (UnicodeError, SyntaxError, RecursionError) as exc:
        raise ValueError('invalid_python') from exc
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'required_text']
    if len(functions) != 1:
        raise ValueError('function_scope')
    function = functions[0]
    lines = source.splitlines(keepends=True)
    return function, b''.join(lines[:function.lineno - 1]), b''.join(lines[function.end_lineno:])


def validate_candidate(manifest, source, tests, harness, candidate):
    """Exact outer bytes/signature plus a deliberately tiny non-executed grammar.

    This restricts scope; it is not a Python sandbox. No generated code is run.
    """
    prepare(manifest, source, tests, harness)
    if (type(candidate) is not bytes or not 0 < len(candidate) <= manifest.max_source_bytes
            or b'\r' in candidate or not candidate.endswith(b'\n')):
        raise ValueError('candidate_bound')
    old, prefix, suffix = _function(source)
    new, new_prefix, new_suffix = _function(candidate)
    if (prefix != new_prefix or suffix != new_suffix or new.decorator_list
            or getattr(new, 'type_params', [])
            or ast.dump(old.args) != ast.dump(new.args)
            or ast.dump(old.returns) != ast.dump(new.returns)):
        raise ValueError('function_scope')
    # Header bytes, including annotations, are immutable, not just AST-equivalent.
    old_header = source.splitlines(keepends=True)[old.lineno - 1]
    if candidate.splitlines(keepends=True)[new.lineno - 1] != old_header:
        raise ValueError('signature_bytes')
    validate_function(candidate_function(candidate).decode('utf-8'))
    if candidate == source:
        raise ValueError('unchanged_candidate')
    return digest(candidate)


def review_patch(manifest, source, tests, harness, candidate):
    """Return a proposed diff only, with NO test/approval/acceptance claim or apply."""
    validate_candidate(manifest, source, tests, harness, candidate)
    return ''.join(difflib.unified_diff(
        source.decode().splitlines(keepends=True), candidate.decode().splitlines(keepends=True),
        fromfile='a/' + SOURCE_PATH, tofile='b/' + SOURCE_PATH)).encode()


def candidate_function(candidate):
    function, _, _ = _function(candidate)
    return b''.join(candidate.splitlines(keepends=True)[function.lineno - 1:function.end_lineno])
