"""Offline-only workspace acceptance contract; no materializer or launch entrypoint."""
import hashlib
import json
from pathlib import Path
import re
import tomllib

BASE_COMMIT = '60da3530a7a694a45d456ae213f4da829c6a06a3'
FAKE_WORKER_PATCH = 'ce577a6e9c9cb123bae66e4c6b800a11f751434f54b81cbc3e20409ccbfc33f7'
PROFILE = '''default_permissions = "zen53_workspace_fixture"
allowed_approval_policies = ["never"]
[allowed_permission_profiles]
zen53_workspace_fixture = true
[permissions.zen53_workspace_fixture.filesystem]
":root" = "read"
"/workspace-fixture" = "write"
[permissions.zen53_workspace_fixture.network]
enabled = false
'''
TOKENS = ('outside_write', 'traversal_write', 'symlink_write', 'secret_read', 'ipv4_socket', 'ipv6_socket')


def need(ok):
    if not ok:
        raise ValueError('WORKSPACE_CONTRACT_REJECTED')


def pair(value):
    need(type(value) is list and len(value) == 2 and all(type(n) is int and 0 < n < 2**63 for n in value))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def plan(stage, workspace_identity):
    need(type(stage) is str and re.fullmatch('/tmp/zen53-workspace-[a-z0-9]{12,32}', stage) is not None)
    pair(workspace_identity)
    payload = Path(__file__).with_name('payload.py').read_bytes()
    # A proposal, not accepted Codex configuration: target-version parser and
    # projected builtin import must be checked before materialization.
    profile = tomllib.loads(PROFILE)
    need(profile['permissions']['zen53_workspace_fixture']['filesystem'] == {':root':'read','/workspace-fixture':'write'})
    return {'state':'OFFLINE_UNMATERIALIZED', 'base_commit':BASE_COMMIT,
            'fake_worker_patch':FAKE_WORKER_PATCH, 'profile':PROFILE,
            'payload_sha256':hashlib.sha256(payload).hexdigest(),
            'workspace':{'source':stage+'/workspace','target':'/workspace-fixture','identity':workspace_identity},
            'unit_amendment':{'BindPaths':stage+'/workspace:/workspace-fixture',
                              'ReadWritePaths':'+/workspace-fixture'},
            'fixture':{'files':['calc.py','test_calc.py'], 'symlink':{'escape':'/readonly-fixture/outside.txt'},
                       'readonly_canary':'/readonly-fixture/outside.txt','secret_canary':'/secret-fixture/canary'},
            'outside_payload':['dispatch','grant','claim','result_publication'],
            'unchanged':['HOME','CODEX_HOME','PrivateNetwork','RestrictAddressFamilies','resource_limits','cleanup_hold'],
            'materialization_gates':['TARGET_CODEX_PROFILE_PARSER','PROJECTED_BUILTIN_SOCKET_IMPORT',
                                     'TRUSTED_STAGE_BINDINGS_AND_PREPOST_OBSERVER','REVIEWED_NATIVE_LAUNCHER_INTEGRATION']}


def validate_mounts(rows, expected_identity):
    """Additional exact workspace rule on normalized authenticated mount observations.

    Caller must retain existing native parser/visibility/alias/propagation checks;
    these rows alone are NOT a substitute for /proc or held-FD corroboration.
    """
    pair(expected_identity)
    need(type(rows) is list and 0 < len(rows) <= 512)
    workspace = []
    for row in rows:
        need(type(row) is dict and set(row) == {'target','identity','writable','type','private'})
        need(type(row['target']) is str and type(row['writable']) is bool and row['private'] is True)
        pair(row['identity'])
        target = row['target']
        need(not target.startswith('/workspace-fixture/'))
        if target == '/workspace-fixture':
            workspace.append(row)
            need(row['identity'] == expected_identity and row['writable'])
        else:
            need(row['identity'] != expected_identity)  # No second visible workspace alias.
            need(not row['writable'] or (row['type'],target) in
                 (('proc','/proc'),('devpts','/dev/pts'),('tmpfs','/run/zen53-bookkeeping')))
    need(len(workspace) == 1)


def verify_observations(record, reference_before, reference_after):
    """Pure correlation. External trusted references are mandatory, never inferred."""
    keys={'stage_sha256','workspace_identity','outside_identity','secret_identity',
          'outside_sha256','secret_sha256','symlink_target','payload_sha256','nonce'}
    need(type(reference_before) is dict and set(reference_before)==keys and canonical(reference_before)==canonical(reference_after))
    for name in ('workspace_identity','outside_identity','secret_identity'):
        pair(reference_before[name])
    need(len({tuple(reference_before[n]) for n in ('workspace_identity','outside_identity','secret_identity')})==3)
    for name in ('stage_sha256','outside_sha256','secret_sha256','payload_sha256','nonce'):
        need(type(reference_before[name]) is str and re.fullmatch('[0-9a-f]{64}',reference_before[name]) is not None)
    need(reference_before['symlink_target']=='/readonly-fixture/outside.txt')
    need(type(record) is dict and set(record)=={'schema_version','state','tests_passed','probes','binding'})
    need(type(record['schema_version']) is int and record['schema_version']==1
         and record['state']=='PAYLOAD_OBSERVATIONS_ONLY' and type(record['tests_passed']) is int and record['tests_passed']==2)
    need(type(record['binding']) is dict and record['binding'] == {k: reference_before[k] for k in ('nonce','stage_sha256','payload_sha256')})
    need(type(record['probes']) is list and len(record['probes'])==len(TOKENS))
    for token, item in zip(TOKENS,record['probes']):
        need(type(item) is dict and set(item)=={'probe','errno'} and item['probe']==token and type(item['errno']) is int)
        allowed=(1,13,30) if token.endswith('write') else (1,13) if token=='secret_read' else (1,13,97)
        need(item['errno'] in allowed)
    return {'state':'SYNTHETIC_CORRELATION_ONLY','record_sha256':hashlib.sha256(canonical(record)).hexdigest(),
            'references_sha256':hashlib.sha256(canonical(reference_before)).hexdigest(),
            'os_isolation_acceptance':'UNTESTED'}
