"""Offline-only property comparison. Never called by the bridge or launcher."""
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
