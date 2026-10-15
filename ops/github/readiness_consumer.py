"""Inactive runner-side evidence state machine; no CLI, transport or status writer.

Only trusted workflow code may supply authenticated run/main lookups and probe bytes.
Never invoke this with downloaded artifact bytes as a substitute for transport.
"""
from dataclasses import dataclass
import math
import secrets

from ops.vps.api_readiness import DIGEST, SHA, validate_evidence

REPOSITORY = 'stevengalvis/modelfc'
REQUIRED_JOBS = frozenset({'test', 'frontend', 'release_offline', 'deploy'})
MAX_SECONDS = 35 * 60
MAX_ATTEMPTS = 71


@dataclass(frozen=True)
class ReleaseRun:
    repository: str
    sha: str
    run_id: int
    run_attempt: int
    event: str
    ref: str

    def validate(self, jobs):
        if (self.repository != REPOSITORY or not isinstance(self.sha, str)
                or not SHA.fullmatch(self.sha) or type(self.run_id) is not int
                or self.run_id <= 0 or type(self.run_attempt) is not int
                or self.run_attempt <= 0 or self.event != 'push'
                or self.ref != 'refs/heads/main' or not isinstance(jobs, dict)
                or set(jobs) != REQUIRED_JOBS
                or any(value != 'success' for value in jobs.values())):
            raise ValueError('release prerequisites rejected')


class ReadinessSession:
    """One ephemeral instance per authenticated release run/attempt.

    The adapter must obtain jobs for this exact run/attempt and enforce bounded
    authenticated SSH reads (4096 bytes, <=20s), pinned host and trusted helper hash.
    All times/main values are runner observations, never supplied by the helper.
    No cached instance/result may authorize another deployment, including same SHA.
    """
    def __init__(self, run, jobs, helper_sha256, *, monotonic_now):
        run.validate(jobs)
        if not isinstance(helper_sha256, str) or not DIGEST.fullmatch(helper_sha256):
            raise ValueError('helper pin rejected')
        self.run = run
        self.helper_sha256 = helper_sha256
        self._last_tick = self._tick(monotonic_now)
        self._deadline = self._last_tick + MAX_SECONDS
        self._nonces = set()
        self._pending = None
        self._candidate = None
        self._closed = False

    @staticmethod
    def _tick(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid monotonic clock')
        return value

    def _check(self, current_main_sha, monotonic_now):
        try:
            tick = self._tick(monotonic_now)
            if (self._closed or current_main_sha != self.run.sha
                    or tick < self._last_tick or tick >= self._deadline):
                raise ValueError('closed, superseded or expired session')
            self._last_tick = tick
        except ValueError:
            self._closed = True
            self._pending = self._candidate = None
            raise

    def begin(self, *, current_main_sha, now, monotonic_now):
        self._check(current_main_sha, monotonic_now)
        if type(now) is not int or now < 0 or len(self._nonces) >= MAX_ATTEMPTS:
            self._closed = True
            raise ValueError('attempt limit or invalid wall clock')
        # Beginning another attempt discards all previous evidence/challenges.
        self._pending = self._candidate = None
        nonce = secrets.token_hex(32)
        if not DIGEST.fullmatch(nonce) or nonce in self._nonces:
            self._closed = True
            raise ValueError('nonce generation failed')
        self._nonces.add(nonce)
        self._pending = (nonce, now)
        return {'sha': self.run.sha, 'nonce': nonce}

    def accept(self, raw, *, current_main_sha, now, monotonic_now):
        self._check(current_main_sha, monotonic_now)
        pending, self._pending = self._pending, None
        self._candidate = None
        if pending is None:
            raise ValueError('no outstanding challenge')
        nonce, started = pending
        # Consume the challenge even on missing, malformed, failed or stale proof.
        value = validate_evidence(raw, expected_sha=self.run.sha,
            current_main_sha=current_main_sha, expected_nonce=nonce,
            expected_helper_sha256=self.helper_sha256, request_started_at=started, now=now)
        self._candidate = (raw, nonce, started)
        return value  # Validated observation only; not a publication authorization.

    def finish(self, *, current_main_sha, now, monotonic_now):
        """One-shot final main/freshness check immediately before future publication."""
        self._check(current_main_sha, monotonic_now)
        candidate, self._candidate = self._candidate, None
        self._closed = True
        if candidate is None:
            raise ValueError('no validated observation')
        raw, nonce, started = candidate
        value = validate_evidence(raw, expected_sha=self.run.sha,
            current_main_sha=current_main_sha, expected_nonce=nonce,
            expected_helper_sha256=self.helper_sha256, request_started_at=started, now=now)
        return {'schema_version': 1, 'repository': self.run.repository,
                'sha': self.run.sha, 'run_id': self.run.run_id,
                'run_attempt': self.run.run_attempt, 'evidence': value}
