"""Fixed protected Agents API host factory. Import/construct never read keys.

Explicit operator configuration is required. No installation, migration, timer,
credential creation/copy or model selection from task text. The admitted run uses
only the existing credential reader under its original nonroot identity.
"""
from contextlib import suppress
from dataclasses import fields
import json
import os
from pathlib import Path
import pwd
import subprocess
import time
from types import SimpleNamespace

from .agents_checkout_verifier import CheckoutVerifier, load_plan
from .agents_credentials import ACCOUNT, UID
from .agents_json_oracle import JSONCallPolicy, harness_sha256
from .agents_linear_adapter import AgentsTaskExecutor, _request, load_approval, task_digest
from .agents_live_pilot import PreparedTarget, _key
from .agents_repository_broker import BrokerSpec, canonical, verify_installation
from .agents_repository_runtime import RepositoryRuntime, verify_supervisor
from .bridge_checkout import Checkout
from .bridge_security import disjoint, protected_dir, protected_json
from .linear_pickup import UnsupportedTarget

# Reserved outside Task.task_id's grammar. Provision this root-owned 0700 child
# once; runtime validation never creates it or changes its permissions.
API_CHECKOUT_DIRECTORY = '.agents-api-checkouts'


def api_checkout_root(runtime):
    workspace = protected_dir(runtime['workspace_root'], 0o711)
    return protected_dir(workspace / API_CHECKOUT_DIRECTORY, 0o700)


# Fixed trusted entry only. Values and source never appear in argv or logs.
CREDENTIAL_BOOT = '''import json,os,sys
sys.path.insert(0,sys.argv[1])
from ops.bounded_worker.agents_credentials import read_credentials
try:
    application,environment = read_credentials()
    raw = json.dumps({'application':application,'environment':environment},sort_keys=True,separators=(',',':')).encode('ascii')
    if len(raw)>16384: raise ValueError()
    view=memoryview(raw)
    while view: view=view[os.write(1,view):]
except BaseException:
    raise SystemExit(73)
'''


def load_api_settings(path, runtime, mirror):
    value = protected_json(path)
    required = {'version', 'enabled', 'broker', 'api_approval', 'verification_plan', 'oracle_policy'}
    if (type(value) is not dict or set(value) != required or type(value['version']) is not int
            or value['version'] != 1 or value['enabled'] is not True):
        raise UnsupportedTarget('api_configuration_not_enabled')
    raw = value['broker']
    if (type(raw) is not dict or set(raw) != {f.name for f in fields(BrokerSpec)}
            or type(raw['target']) is not dict or set(raw['target']) != {f.name for f in fields(PreparedTarget)}):
        raise ValueError('api_broker_settings')
    spec = BrokerSpec(**(raw | {'target': PreparedTarget(**raw['target'])})); spec.validate()
    account = pwd.getpwnam(ACCOUNT)
    if (spec.uid, spec.gid) != (account.pw_uid, account.pw_gid) or spec.uid != UID:
        raise ValueError('existing_api_credential_identity_required')
    roots = [protected_dir(mirror), protected_dir(runtime['workspace_root'], 0o711),
             protected_dir(runtime['evidence_root'], 0o700), protected_dir(runtime['state_root'], 0o700)]
    for index, root in enumerate(roots): disjoint(root, *roots[index+1:])
    # The only intentional nesting is the API's private child of the common
    # workspace. It cannot alias a mirror, control, state or evidence directory.
    api_checkout_root(runtime)
    for name in ('api_approval', 'verification_plan', 'oracle_policy'):
        item = value[name]
        if type(item) is not str or not Path(item).is_absolute(): raise ValueError('api_control_path')
        protected_dir(Path(item).parent)
        disjoint(item, *roots, spec.code_root, spec.target.runtime_home, spec.target.runtime_dir)
    disjoint(path, *roots, spec.code_root, spec.target.runtime_home, spec.target.runtime_dir)
    disjoint(spec.code_root, *roots)
    return value, spec


def read_existing_keys(spec):
    """One fixed owner-context read; no saving, discovery, login or API request."""
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError('root_api_supervisor_required')
    executable = verify_installation(spec)
    process = subprocess.Popen([executable, '-I', '-S', '-B', '-c', CREDENTIAL_BOOT, spec.code_root],
        user=spec.uid, group=spec.gid, extra_groups=(), cwd='/',
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    try:
        # Pinned helper itself caps output before writing; no arbitrary code endpoint.
        raw, _ = process.communicate(timeout=5)
        if process.returncode != 0 or len(raw) > 16384:
            raise ValueError('api_credential_read_failed')
        from .bridge_envelope import _pairs
        value = json.loads(raw, object_pairs_hook=_pairs)
        if type(value) is not dict or set(value) != {'application', 'environment'} or canonical(value) != raw:
            raise ValueError('api_credential_frame')
        app, env = value['application'], value['environment']
        _key(app); _key(env)
        if app == env: raise ValueError('distinct_api_keys_required')
        return app, env
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=2)
        process.stdout.close()


class GuardedRuntime:
    def __init__(self, inner, guard): self.inner, self.guard = inner, guard
    @property
    def runtime_sha256(self):
        self.guard()
        return self.inner.runtime_sha256
    def __getattr__(self, name): return getattr(self.inner, name)


class FixedAPIExecutor:
    target = 'agents-api'

    def __init__(self, db, path, runtime, mirror, *, guard=None, clock=time.time):
        self.db, self.path, self.runtime, self.mirror = db, path, runtime, mirror
        self.guard, self.clock = guard, clock
        self.loaded, self.spec = load_api_settings(path, runtime, mirror)
        self.config = SimpleNamespace(workspace_root=runtime['workspace_root'], evidence_root=runtime['evidence_root'])

    def unchanged(self):
        if self.guard is not None: self.guard()
        if load_api_settings(self.path, self.runtime, self.mirror) != (self.loaded, self.spec):
            raise ValueError('api_settings_changed')

    def run(self, task):
        inner = None
        app = env = None
        try:
            self.unchanged()
            row = self.db.execute('SELECT * FROM linear_coding_jobs WHERE task=?', (task.task_id,)).fetchone()
            if row is None or row[5] != 'launching' or _request(row)[2] != task:
                raise ValueError('shared_dispatch_required')
            ack = self.db.execute('SELECT phase FROM linear_coding_acknowledgments WHERE job=?', (row[0],)).fetchone()
            picked = self.db.execute('SELECT target,acknowledged,running FROM linear_coding_pickups WHERE job=?', (row[0],)).fetchone()
            if (ack != ('posted',) or picked is None or picked[0] != 'agents-api'
                    or type(picked[1]) is not int or type(picked[2]) is not int or picked[2] < picked[1]):
                raise ValueError('confirmed_pickup_required')
            approval = load_approval(self.loaded['api_approval']); plan = load_plan(self.loaded['verification_plan'])
            policy = JSONCallPolicy(canonical(protected_json(self.loaded['oracle_policy'])))
            selected_policy = policy.value()
            if (approval.enabled is not True or not int(self.clock()) < approval.expires
                    or approval.task_sha256 != task_digest(task) or approval.runtime_sha256 != self.spec.runtime_sha256
                    or approval.acceptance_sha256 != self.spec.target.acceptance_id
                    or plan.enabled is not True or plan.task_sha256 != task_digest(task)
                    or plan.runtime_sha256 != self.spec.runtime_sha256 or plan.harness_sha256 != harness_sha256(policy)
                    or selected_policy['task_sha256'] != task_digest(task)
                    or plan.test_image_sha256 != selected_policy['image_sha256']
                    or plan.test_acceptance_sha256 != selected_policy['acceptance_sha256']
                    or plan.max_seconds != selected_policy['max_seconds']
                    or plan.max_output_bytes != selected_policy['max_output_bytes']
                    or plan.max_output_bytes > task.output_bytes
                    or plan.test_names != tuple(case['name'] for case in selected_policy['cases'])):
                raise ValueError('api_task_not_authorized')
            # Reject wrong source/install before any credential is read.
            verify_supervisor(self.spec); verify_installation(self.spec)
            checked = CheckoutVerifier(self.db, Checkout(self.mirror, api_checkout_root(self.runtime)),
                                       self.runtime['evidence_root'], self.loaded['verification_plan'])
            app, env = read_existing_keys(self.spec)
            inner = RepositoryRuntime(self.spec, checked, self.loaded['oracle_policy'],
                                      application_key=app, environment_key=env)
            app = env = None
            selected = GuardedRuntime(inner, self.unchanged)
            return AgentsTaskExecutor(self.db, self.loaded['api_approval'], selected, clock=self.clock).run(task)
        except BaseException:
            return {'status': 'blocked', 'task_id': task.task_id}
        finally:
            app = env = None
            if inner is not None:
                with suppress(BaseException): inner.close()
