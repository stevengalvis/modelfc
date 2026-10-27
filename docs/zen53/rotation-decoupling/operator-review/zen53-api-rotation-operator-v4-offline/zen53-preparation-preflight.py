"""Preparation-only source scan using the exact pinned builder reader.

Intended for clean /usr/bin/python3.12 -I -S -B - with five positional args:
package root, recovery root, canonical package archive, inventory hash,
package archive hash. It makes no stage and executes no operational entrypoint.
"""
import os
import re
import stat
import sys


def routes(plan):
    rows = [('regular:' + path, pin)
            for path, pin in sorted(plan['regular'].items())]
    rows += [('tool:' + name, pin)
             for name, pin in sorted(plan['tool'].items())]
    rows.append(('collector', plan['collector']))
    if len(rows) != 62 or len({pin['source'] for _, pin in rows}) != 62:
        raise ValueError('PREFLIGHT_ROUTE_SET')
    return rows


def failure_code(exc):
    if type(exc) is ValueError and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', str(exc)):
        return str(exc)
    if type(exc) in (OSError, FileNotFoundError, PermissionError,
                     IsADirectoryError, NotADirectoryError, TimeoutError,
                     RuntimeError):
        number = getattr(exc, 'errno', None)
        suffix = f'_{number}' if type(number) is int and 0 <= number <= 4095 else ''
        return type(exc).__name__ + suffix
    return 'UNKNOWN'


def main(args):
    if len(args) != 5 or os.geteuid() != 0 or os.getegid() != 0:
        raise ValueError('PREFLIGHT_ARGUMENTS')
    package, recovery, archive, inventory_sha, archive_sha = args
    sys.path.insert(0, package)
    import stage_builder as builder
    import operator_entry as entry

    reader = builder.FileSource(0)
    reader.read_small(archive, archive_sha, maximum=1048576,
                      expected_uid=0, expected_gid=0, expected_mode=0o444)
    inventory = reader.read_small(package + '/INVENTORY.json', inventory_sha,
                                  maximum=262144, expected_uid=0,
                                  expected_gid=0, expected_mode=0o444)
    recovery_contract = reader.read_small(
        package + '/recovery_contract.json', builder.RECOVERY_SHA256,
        maximum=524288, expected_uid=0, expected_gid=0,
        expected_mode=0o444)
    manifest = reader.read_small(
        package + '/runtime_source_manifest.json',
        builder.restricted.RUNTIME_MANIFEST_SHA256, maximum=262144,
        expected_uid=0, expected_gid=0, expected_mode=0o444)
    dependencies = reader.read_small(
        package + '/runtime_dependencies.json',
        builder.restricted.RUNTIME_DEPENDENCIES_SHA256, maximum=262144,
        expected_uid=0, expected_gid=0, expected_mode=0o444)
    plan = builder.make_plan(package, recovery, inventory, recovery_contract,
                             manifest, dependencies,
                             expected_inventory_sha=inventory_sha)
    sink = os.open('/dev/null', os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(sink)
        if not stat.S_ISCHR(info.st_mode) or (os.major(info.st_rdev),
                                              os.minor(info.st_rdev)) != (1, 3):
            raise ValueError('PREFLIGHT_SINK')
        observations = {}
        for index, (label, pin) in enumerate(routes(plan), 1):
            try:
                observations[label.removeprefix('regular:')] = reader.copy(
                    pin['source'], sink, pin['sha256'],
                    maximum=pin['maximum'], expected_uid=pin['source_uid'],
                    expected_gid=pin['source_gid'],
                    expected_mode=pin['source_mode'])
            except Exception as exc:
                print(f'PREFLIGHT_BLOCK route={index:02d}/62 code={failure_code(exc)}',
                      file=sys.stderr)
                raise SystemExit(2) from None
        builder._release_observations(observations, plan)
    finally:
        os.close(sink)

    # Prospective roots are existing new package/recovery roots on /var/lib.
    # The builder rechecks the actual created stage and tool later.
    paths = entry._backing_paths(package, recovery)
    source_devices = {f'{os.major(os.stat(p).st_dev)}:'
                      f'{os.minor(os.stat(p).st_dev)}' for p in paths}
    devices = tuple(sorted({entry._verify_direct_block_topology(dev)[0]
                            for dev in source_devices}))
    snapshot = entry._verified_backing(paths, devices)
    if set(snapshot['origin_nodes']) != set(devices):
        raise ValueError('PREFLIGHT_ORIGINS')
    print('PREFLIGHT_PASS routes=62 origins=' + ','.join(devices))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception as exc:
        print('PREFLIGHT_BLOCK route=CONTROL_OR_TOPOLOGY code=' + failure_code(exc),
              file=sys.stderr)
        raise SystemExit(2) from None
