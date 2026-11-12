"""Explicit repository implementation of AgentsTaskExecutor callbacks.

Root owns the Linear store, provider application key and independent verifier.
A fixed, pinned nonroot helper owns rootless containment and receives only the
separate environment key. The separate host factory supplies keys; this module performs no lookup or action on import.
"""
from pathlib import Path
import hashlib
import asyncio

from .agents_connection import AgentsConnection
from .agents_linear_adapter import task_digest
from .agents_live_pilot import PreparedTarget, _key
from .agents_repository_broker import RootlessBroker, protected_directory, protected_file
from .agents_repository_events import TaskSDKEvents
from .agents_repository_snapshot import canonical, encode_snapshot
from .bridge_security import protected_json


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def supervisor_digest():
    """Bind every top-level Python component in the installed root supervisor.

    This includes verifier, transport, adapter, checkout, patch and all shared
    admission helpers, not just the nonroot child closure. The installed source
    directory and ancestors must be root-owned and not group/world-writable.
    """
    root = protected_directory(Path(__file__).parent)
    records = [(path.name,protected_file(path,1048576)) for path in sorted(root.glob('*.py'))]
    if not records: raise ValueError('supervisor_code_missing')
    return digest(canonical(records))


def verify_supervisor(spec):
    if supervisor_digest() != spec.supervisor_sha256:
        raise ValueError('supervisor_code_pin')


class RepositoryRuntime:
    """Concrete single-attempt runtime; explicit accepted inputs are mandatory.

    Factories are offline test seams, never selected by a Task. The normal path
    launches the fixed rootless broker and calls the actual official transports.
    Such use needs separate host/profile/API/spend and source-sharing approval.
    """
    def __init__(self,spec,verifier,oracle_policy_path, *, application_key,environment_key,
                 broker_factory=None,connection_factory=None,events_factory=None,supervisor_check=None):
        spec.validate()
        _key(application_key); _key(environment_key)
        if application_key == environment_key: raise ValueError('separate_environment_key_required')
        self.spec,self.verifier,self.oracle_policy_path = spec,verifier,oracle_policy_path
        self._supervisor_check = supervisor_check or verify_supervisor
        self._supervisor_check(spec)
        self.acceptance_sha256 = spec.target.acceptance_id
        self._application_key,self._environment_key = application_key,environment_key
        self._broker_factory = broker_factory or RootlessBroker
        self._connection_factory = connection_factory or AgentsConnection
        self._events_factory = events_factory or TaskSDKEvents
        self.task = self.attempt = self.binding = self.broker = self.policy = None
        self.connection = self.events = None
        self.empty = False
        self._closed,self._close_result = False,False

    @property
    def runtime_sha256(self):
        self._supervisor_check(self.spec)
        return self.spec.runtime_sha256

    def prepare(self,task,attempt):
        if self.task is not None: raise ValueError('repository_prepare_replayed')
        self.task,self.attempt = task,attempt
        self._supervisor_check(self.spec)
        from .agents_json_oracle import JSONCallPolicy, harness_sha256
        task.validate()
        if task.output_bytes > 65536: raise ValueError('repository_api_output_limit')
        self.policy = JSONCallPolicy(canonical(protected_json(self.oracle_policy_path)))
        value = self.policy.value()
        files = self.verifier.prepare(task,attempt)
        plan = self.verifier.plan
        if (plan.runtime_sha256 != self.runtime_sha256 or plan.harness_sha256 != harness_sha256(self.policy)
                or value['task_sha256'] != task_digest(task) or plan.max_seconds != value['max_seconds']
                or plan.max_output_bytes != value['max_output_bytes']
                or plan.test_image_sha256 != value['image_sha256']
                or plan.test_acceptance_sha256 != value['acceptance_sha256']
                or tuple(case['name'] for case in value['cases']) != plan.test_names):
            raise ValueError('repository_oracle_binding')
        source = encode_snapshot(files,task_digest(task),attempt,self.verifier.baseline)
        # Verify transport dependencies and exact Task output cap before create.
        self.connection = self._connection_factory(self._application_key,timeout=10,max_bytes=task.output_bytes)
        self.events = self._events_factory(self._application_key,task.output_bytes)
        self._application_key = None
        self.broker = self._broker_factory(self.spec)
        try:
            self.broker.prepare(source,self._environment_key,(task_digest(task),attempt,self.verifier.baseline))
        finally:
            self._environment_key = None

    def start(self,binding):
        if self.binding is not None or binding.attempt_id != self.attempt:
            raise ValueError('repository_start_replayed')
        self.binding = binding
        self.broker.start(binding)

    def stop(self,binding):
        if binding != self.binding: raise ValueError('repository_binding')
        self.broker.stop(binding)

    def verify_empty(self,binding):
        if binding != self.binding: return False
        self.empty = self.broker.verify_empty(binding) is True
        return self.empty

    def verify(self,task,attempt,binding,turn):
        if (not self.empty or task != self.task or attempt != self.attempt or binding != self.binding
                or self.broker.raw_candidate is None):
            raise ValueError('repository_cleanup_before_verification')
        from .agents_checkout_verifier import _write_new
        try:
            if canonical(protected_json(self.oracle_policy_path)) != self.policy.raw:
                raise ValueError('repository_oracle_policy_changed')
            stage = self.verifier.stage_candidate(self.broker.raw_candidate,binding,turn)
            plan = self.verifier.plan
            files = self.verifier.original | self.verifier.derived
            test_attempt = digest(canonical(dict(stage_sha256=digest(canonical(stage)),
                                                policy_sha256=self.policy.sha256)))
            _write_new(self.verifier.evidence/'test-reservation.json',canonical(dict(
                test_attempt_id=test_attempt,stage_sha256=digest(canonical(stage)),
                policy_sha256=self.policy.sha256)))
            target = PreparedTarget(plan.test_image_sha256,self.spec.target.runtime_home,
                                    self.spec.target.runtime_dir,plan.test_acceptance_sha256)
            receipt,evidence = self.broker.run_tests(target,plan,stage,files,self.policy,test_attempt)
            raw = canonical(evidence)
            if type(evidence) is not dict or len(raw) > 65536:
                raise ValueError('repository_test_evidence_limit')
            _write_new(self.verifier.evidence/'test-execution.json',raw)
            if (type(receipt) is not dict or receipt.get('evidence_sha256') != digest(raw)
                    or receipt.get('test_attempt_id') != test_attempt):
                raise ValueError('repository_tests_unverified')
            receipt_raw = canonical(receipt)
            if len(receipt_raw) > 65536: raise ValueError('repository_test_receipt_limit')
            receipt_path = self.verifier.evidence/'oracle-receipt.json'
            _write_new(receipt_path,receipt_raw)
            if canonical(protected_json(self.oracle_policy_path)) != self.policy.raw:
                raise ValueError('repository_oracle_policy_changed')
            return self.verifier.finish(receipt_path,binding,turn)
        finally:
            self.close()

    def close(self):
        if self._closed: return self._close_result
        self._closed = True
        result = True
        try:
            if self.broker is not None: result = self.broker.close() is True
        except BaseException:
            result = False
        finally:
            self._application_key = self._environment_key = None
            if self.events is not None and getattr(self.events,'closed',False) is not True:
                async def close_events():
                    await asyncio.wait_for(self.events.close(),timeout=5)
                try: asyncio.run(close_events())
                except BaseException: result = False
        self._close_result = result
        return result
