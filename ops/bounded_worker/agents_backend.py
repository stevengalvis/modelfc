"""Offline isolated-executor contract with durable, one-shot mock dispatch.

No default runner, credential input, API call or OS containment implementation.
Injected callbacks are trusted, single-controller test seams, not attestation.
Never supply a bare-host executor or the production systemd launcher here.
"""
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re

from ops.bounded_worker.agents_admission import AdmissionJournal


def _matches(pattern, value):
    return type(value) is str and re.fullmatch(pattern, value) is not None


@dataclass(frozen=True)
class LaunchSpec:
    attempt_id: str
    session_id: str
    environment_id: str
    image_digest: str
    executable_sha256: str
    workspace_sha256: str
    uid: int
    gid: int
    timeout_seconds: int = 180

    def manifest(self):
        """Data-only requirements, NOT executable arguments or host settings."""
        prepare(self)
        return {
            'schema': 'agents-isolated-executor-v1', **asdict(self),
            'unit_name': self.unit_name,
            'executable': '/opt/agents/bin/codex', 'cwd': '/workspace',
            'home': '/tmp/agents-home', 'memory_bytes': 1073741824,
            'tasks_max': 64, 'cpu_percent': 100, 'stop_timeout_seconds': 5,
            'network': 'separate-control-channel-command-network-denied',
            'credential_channel': 'restricted-environment-key-inherited-pipe',
            'application_key': 'excluded', 'host_mounts': 'none',
            'root_filesystem': 'immutable-image', 'home_state': 'fresh-private',
            'workspace': 'disposable-copy', 'privilege_escalation': 'denied',
            'command_sandbox': 'required', 'raw_output': 'discard',
        }

    @property
    def unit_name(self):
        prepare(self)
        return 'agents-api-' + self.attempt_id + '.service'

    @property
    def spec_sha256(self):
        encoded = json.dumps(self.manifest(), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(encoded.encode('ascii')).hexdigest()

    @property
    def binding(self):
        return ExecutorBinding(self.attempt_id, self.session_id, self.environment_id,
                               self.unit_name, self.image_digest, self.spec_sha256)


def prepare(spec):
    """Pure validation only. Does not read an image, host or credential."""
    if type(spec) is not LaunchSpec:
        raise ValueError('invalid_launch_spec')
    if (any(not _matches(r'[a-f0-9]{64}', value) for value in
            (spec.attempt_id, spec.executable_sha256, spec.workspace_sha256))
            or not _matches(r'sha256:[a-f0-9]{64}', spec.image_digest)
            or any(not _matches(r'[A-Za-z0-9_-]{1,128}', value) for value in
                   (spec.session_id, spec.environment_id))
            or any(type(value) is not int or not 1 <= value <= 2147483647
                   for value in (spec.uid, spec.gid))
            or type(spec.timeout_seconds) is not int or not 1 <= spec.timeout_seconds <= 180):
        raise ValueError('invalid_launch_spec')
    return spec


@dataclass(frozen=True)
class ExecutorBinding:
    attempt_id: str
    session_id: str
    environment_id: str
    unit_name: str
    image_digest: str
    spec_sha256: str


@dataclass(frozen=True)
class CapabilityEvidence:
    binding: ExecutorBinding
    image_verified: bool = False
    disposable_workspace_verified: bool = False
    filesystem_isolation_verified: bool = False
    clean_home_verified: bool = False
    nonroot_verified: bool = False
    no_new_privileges_verified: bool = False
    resource_limits_verified: bool = False
    command_sandbox_verified: bool = False
    control_channel_separated: bool = False
    command_network_denied: bool = False
    restricted_key_pipe_verified: bool = False
    application_key_excluded: bool = False
    raw_output_suppressed: bool = False
    descendants_cleanup_supported: bool = False


@dataclass(frozen=True)
class LaunchObservation:
    binding: ExecutorBinding
    started: bool = False


@dataclass(frozen=True)
class StopObservation:
    binding: ExecutorBinding
    stopped: bool = False


@dataclass(frozen=True)
class EmptyObservation:
    binding: ExecutorBinding
    unit_inactive: bool = False
    cgroup_empty: bool = False


class OfflineExecutorBackend:
    """Contract exerciser, not a usable live backend or sandbox.

    Every callback receives the same immutable spec. inspect must observe the
    already prepared sandbox, and a future launcher must preserve that identity
    atomically through start; booleans here do not establish that property.
    The journal must be private, durable, trusted and outside the sandbox.
    Only mock callbacks are implemented/reviewed for this candidate.
    """
    def __init__(self, journal, *, inspect, launch, terminate, observe_empty):
        if (type(journal) is not AdmissionJournal
                or any(not callable(value) for value in (inspect, launch, terminate, observe_empty))):
            raise ValueError('explicit_backend_dependencies_required')
        self._journal = journal
        self._inspect, self._launch = inspect, launch
        self._terminate, self._observe_empty = terminate, observe_empty
        self._spec = None
        self.attempted = False
        self.may_have_started = False
        self.started = False
        self.stop_attempted = False
        self.stopped = False

    def _bound(self, observation, expected_type):
        return (type(observation) is expected_type
                and type(observation.binding) is ExecutorBinding
                and all(type(getattr(observation.binding, field.name)) is str
                        for field in fields(ExecutorBinding))
                and observation.binding == self._spec.binding)

    def launch(self, spec):
        if self.attempted:
            raise ValueError('duplicate_executor_launch')
        self.attempted = True
        self._spec = prepare(spec)
        try:
            evidence = self._inspect(spec)
        except Exception:
            raise RuntimeError('backend_capabilities_unverified') from None
        if (not self._bound(evidence, CapabilityEvidence)
                or any(getattr(evidence, field.name) is not True
                       for field in fields(CapabilityEvidence) if field.name != 'binding')):
            raise ValueError('backend_capabilities_unverified')

        def dispatch():
            # The journal has durably committed before this callback begins.
            # A failure from this point can mean a process actually started.
            self.may_have_started = True
            try:
                result = self._launch(spec)
            except Exception:
                raise RuntimeError('executor_start_uncertain') from None
            if not self._bound(result, LaunchObservation) or result.started is not True:
                raise ValueError('executor_start_unconfirmed')
            self.started = True
            return result

        return self._journal.run_once(spec.attempt_id, 'executor', dispatch)

    def terminate(self):
        """One stop invocation even after uncertain start; not proof of emptiness."""
        if not self.may_have_started or self.stop_attempted:
            return False
        self.stop_attempted = True
        try:
            result = self._terminate(self._spec)
            self.stopped = self._bound(result, StopObservation) and result.stopped is True
        except Exception:
            self.stopped = False
        return self.stopped

    def verify_empty(self):
        """Independent readback, never inferred from return code/stop success."""
        # A stop response can be lost after the processes actually terminated.
        # Reconcile by observation without invoking the uncertain stop again.
        if not self.stop_attempted:
            return False
        try:
            result = self._observe_empty(self._spec)
            return (self._bound(result, EmptyObservation) and result.unit_inactive is True
                    and result.cgroup_empty is True)
        except Exception:
            return False
