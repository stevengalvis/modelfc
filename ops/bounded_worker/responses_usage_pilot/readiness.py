"""Bounded read-only sandbox prerequisites; no model, key or container launch."""
import hashlib

from diagnostics import failure_details
from ops.bounded_worker.agents_live_pilot import _bounded_call
from pilot import ATTEMPT, MAX_CALLS
from sandbox import TestExecutor

READINESS_SECONDS = 25
CODES = frozenset(('invalid_call_numbers', 'image_unavailable',
                   'image_identity_mismatch', 'container_name_unavailable',
                   'invalid_readiness_evidence'))


class ReadinessFailure(ValueError):
    def __init__(self, code):
        self.code = code if code in CODES else 'invalid_readiness_evidence'
        super().__init__(self.code)


def readiness_failure(exc):
    return {'code': exc.code if type(exc) is ReadinessFailure else 'operation_failed',
            **failure_details(exc)}


def verify_readiness(target, numbers):
    """Verify exact image and upcoming names before provider access, once.

    Checks can become stale; keep all existing create-time checks. This does not
    attest execution/isolation, reserve names, pull images or authorize a run.
    """
    if (type(numbers) is not tuple or not 1 <= len(numbers) <= MAX_CALLS
            or any(type(n) is not int or not 1 <= n <= MAX_CALLS for n in numbers)
            or len(set(numbers)) != len(numbers)):
        raise ReadinessFailure('invalid_call_numbers')
    executor = TestExecutor(target, 'synthetic-no-key')

    def inspect():
        executor.verify_prepared()
        # Podman 4.9.3 documents .Id as the full image ID, not a tag or manifest
        # digest. Fetch only that field, never labels, environment or raw config.
        code, raw = executor._command([
            'image', 'inspect', '--format', '{{.Id}}', 'sha256:' + target.image_id])
        if type(code) is not int or code != 0:
            raise ReadinessFailure('image_unavailable')
        expected = target.image_id.encode('ascii')
        if type(raw) is not bytes or raw not in (expected, expected + b'\n'):
            raise ReadinessFailure('image_identity_mismatch')
        for number in numbers:
            attempt = hashlib.sha256((ATTEMPT + ':' + str(number)).encode()).hexdigest()
            code, raw = executor._command(['container', 'exists', 'agents-' + attempt])
            # Only Podman's documented absent result is affirmative readiness.
            # Existing, permission/error, malformed and uncertain replies block.
            if type(code) is not int or code != 1 or type(raw) is not bytes or raw != b'':
                raise ReadinessFailure('container_name_unavailable')
        return True

    try:
        if _bounded_call(inspect, READINESS_SECONDS) is not True:
            raise ReadinessFailure('invalid_readiness_evidence')
        return True
    finally:
        executor.close()
