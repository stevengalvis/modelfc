"""Offline-only property comparison. Never called by the bridge or launcher."""
import re

from .storage import service_properties


def candidate(config, *, work, runtime, home):
    """Build a root-read-only alternative for a separately reviewed diagnostic.

    This does not establish effective mounts, isolation, or production readiness.
    """
    original = service_properties(config, work=work, runtime=runtime, home=home)
    replacements = {
        '--property=ProtectSystem=strict': '--property=ReadOnlyPaths=/ /proc/sys',
        '--property=ReadOnlyPaths=/dev /sys': None,
        '--property=ReadWritePaths=-/dev/pts -/dev/mqueue':
            '--property=ReadWritePaths=/proc -/dev/pts -/dev/mqueue',
    }
    fixed = ('--property=PrivateMounts=yes', '--property=MountFlags=private')
    dynamic = ('--property=BindPaths=', '--property=BindReadOnlyPaths=', '--property=TemporaryFileSystem=')
    if (len(original) != 8 or any(original.count(key) != 1 for key in (*replacements, *fixed))
            or any(sum(item.startswith(prefix) for item in original) != 1 for prefix in dynamic)):
        raise ValueError('unexpected_baseline_properties')
    return [replacement for item in original
            if (replacement := replacements.get(item, item)) is not None]


def candidate_with_api_binds(config, *, work, runtime, home):
    """Add fixed read-only API self-binds for offline diagnostic review only."""
    values = candidate(config, work=work, runtime=runtime, home=home)
    prefix = '--property=BindReadOnlyPaths='
    if sum(value.startswith(prefix) for value in values) != 1:
        raise ValueError('unexpected_baseline_properties')
    api = '/dev:/dev:rbind /run:/run:rbind /sys:/sys:rbind /sys/fs/cgroup:/sys/fs/cgroup:rbind '
    return [prefix + api + value[len(prefix):] if value.startswith(prefix) else value for value in values]


def candidate_with_unit_cgroup(config, *, work, runtime, home, unit):
    """Hypothesize a read-only bind for the backend-selected unit's cgroup."""
    if type(unit) is not str or not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit):
        raise ValueError('invalid_candidate_unit')
    values = candidate_with_api_binds(config, work=work, runtime=runtime, home=home)
    prefix = '--property=BindReadOnlyPaths='
    if sum(value.startswith(prefix) for value in values) != 1:
        raise ValueError('unexpected_baseline_properties')
    path = '/sys/fs/cgroup/system.slice/' + unit
    bind = f' {path}:{path}:rbind'
    return [value + bind if value.startswith(prefix) else value for value in values]


def _evidence_component(records):
    """Validate two operator-authenticated, normalized observations.

    This pure helper cannot authenticate source reports or their hashes. The
    trusted operator must do that before normalization; tasks must not supply
    records. Private paths in these records must not be logged or published.
    """
    fields = {'unit', 'invocation', 'target', 'root', 'device', 'type', 'options'}
    if type(records) is not list or len(records) != 2:
        raise ValueError('invalid_cgroup_evidence')
    suffixes, units, invocations, devices, flag_sets = [], set(), set(), set(), set()
    for record in records:
        if type(record) is not dict or set(record) != fields:
            raise ValueError('invalid_cgroup_evidence')
        unit, invocation = record['unit'], record['invocation']
        if (type(unit) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,180}\.service', unit)
                or type(invocation) is not str or not re.fullmatch(r'[0-9a-f]{32}', invocation)
                or type(record['device']) is not str or not re.fullmatch(r'[0-9]{1,10}:[0-9]{1,10}', record['device'])
                or record['type'] != 'cgroup2' or type(record['options']) is not list
                or len(record['options']) > 32
                or any(type(option) is not str or len(option) > 64 for option in record['options'])
                or 'rw' not in record['options'] or 'ro' in record['options']
                or not {'nosuid', 'nodev', 'noexec'} <= set(record['options'])
                or len(set(record['options'])) != len(record['options'])
                or type(record['target']) is not str or len(record['target']) > 4096
                or type(record['root']) is not str or len(record['root']) > 4096):
            raise ValueError('invalid_cgroup_evidence')
        target_prefix = '/sys/fs/cgroup/system.slice/' + unit + '/'
        root_prefix = '/system.slice/' + unit + '/'
        if not record['target'].startswith(target_prefix) or not record['root'].startswith(root_prefix):
            raise ValueError('invalid_cgroup_evidence')
        component = record['target'][len(target_prefix):]
        if (not re.fullmatch(r'[A-Za-z0-9_.-]{1,255}', component) or component in ('.', '..')
                or record['root'][len(root_prefix):] != component):
            raise ValueError('invalid_cgroup_evidence')
        units.add(unit)
        invocations.add(invocation)
        devices.add(record['device'])
        flag_sets.add(frozenset(record['options']))
        suffixes.append(component)
    if (len(units) != 2 or len(invocations) != 2 or len(devices) != 1 or len(flag_sets) != 1
            or suffixes[0] != suffixes[1]):
        raise ValueError('invalid_cgroup_evidence')
    return suffixes[0]


def candidate_with_evidence_cgroup(config, *, work, runtime, home, unit, records):
    """Build a mandatory child bind from two trusted-operator observations only."""
    if type(unit) is not str or not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit):
        raise ValueError('invalid_candidate_unit')
    component = _evidence_component(records)
    values = candidate_with_api_binds(config, work=work, runtime=runtime, home=home)
    prefix = '--property=BindReadOnlyPaths='
    if sum(value.startswith(prefix) for value in values) != 1:
        raise ValueError('unexpected_baseline_properties')
    path = '/sys/fs/cgroup/system.slice/' + unit + '/' + component
    return [value + f' {path}:{path}:rbind' if value.startswith(prefix) else value for value in values]
