import json
from contextlib import ExitStack, nullcontext
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker import production_one_shot as candidate
from ops.bounded_worker.production_fstab_transaction import append_entry
from ops.bounded_worker.production_image_stage import _write_exclusive


class ProductionOneShotTest(unittest.TestCase):
    def test_command_bounds_output_and_timeout(self):
        self.assertEqual(candidate.command([sys.executable, '-c', "print('ok')"], timeout=2), b'ok\n')
        with self.assertRaises(candidate.OperationUncertain):
            candidate.command([sys.executable, '-c', 'print("x" * 70000)'], timeout=2)
        with self.assertRaises(candidate.OperationUncertain):
            candidate.command([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.05)
        with self.assertRaises(candidate.OperationUncertain):
            candidate.command([sys.executable, '-c', 'raise SystemExit(2)'], timeout=2)

    def test_trim_service_rejects_extra_command_or_missing_job(self):
        good = (b'LoadState=loaded\nActiveState=inactive\nJob=\n'
                b'ExecStart={ path=/usr/sbin/fstrim ; argv[]=/usr/sbin/fstrim --listed-in '
                b'/etc/fstab:/proc/self/mountinfo ; ignore_errors=no ; }\n')
        candidate._trim_service(lambda *_a, **_k: good, '/usr/bin/systemctl', '/usr/sbin/fstrim')
        for raw in (good.replace(b'Job=\n', b''), good.replace(b'ignore_errors=no', b'argv[]=evil'),
                    good.replace(b'/proc/self/mountinfo ;', b'/proc/self/mountinfo --all ;')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                candidate._trim_service(lambda *_a, **_k: raw, '/usr/bin/systemctl', '/usr/sbin/fstrim')

    def test_trim_service_accepts_only_pinned_binary_and_recorded_fixed_flags(self):
        template = ('LoadState=loaded\nActiveState=inactive\nJob=\n'
                    'ExecStart={{ path={path} ; argv[]={path} --listed-in '
                    '/etc/fstab:/proc/self/mountinfo{suffix} ; ignore_errors=no ; }}\n')
        calls = []

        def runner_for(path, suffix):
            def runner(argv, **_kwargs):
                calls.append(argv)
                return template.format(path=path, suffix=suffix).encode()
            return runner

        with patch.object(candidate.os.path, 'samefile', return_value=True):
            for path in ('/usr/sbin/fstrim', '/sbin/fstrim'):
                for suffix in ('', ' --verbose --quiet-unsupported'):
                    candidate._trim_service(runner_for(path, suffix), '/usr/bin/systemctl', '/usr/sbin/fstrim')
            for suffix in (' --quiet-unsupported', ' --verbose --quiet-unsupported --all', ' --verbose --help'):
                with self.assertRaises(ValueError):
                    candidate._trim_service(runner_for('/sbin/fstrim', suffix), '/usr/bin/systemctl', '/usr/sbin/fstrim')
            with self.assertRaises(ValueError):
                candidate._trim_service(runner_for('/tmp/fstrim', ''), '/usr/bin/systemctl', '/usr/sbin/fstrim')
        with patch.object(candidate.os.path, 'samefile', return_value=False):
            with self.assertRaises(ValueError):
                candidate._trim_service(runner_for('/sbin/fstrim', ''), '/usr/bin/systemctl', '/usr/sbin/fstrim')
        self.assertTrue(all('--all' in call for call in calls))

    def test_parser_rejects_unknown_and_duplicate_rows(self):
        image = '/srv/modelfc-bounded-images/worker-' + 'a' * 32 + '.img'
        target = '/srv/modelfc-bounded-' + 'a' * 32
        entry = {'source': image, 'target': target, 'fstype': 'ext4',
                 'options': 'noauto,loop,nosuid,nodev,nodiscard,X-fstrim.notrim'}
        def parse(rows):
            def runner(argv, **_kwargs):
                if argv[0] == '/usr/bin/realpath':
                    return (argv[-1] + '\n').encode()
                return json.dumps({'filesystems': rows}).encode()
            return candidate._parser(runner, '/usr/bin/findmnt', '/usr/bin/realpath', image, target, '/dev/loop0')
        self.assertTrue(parse([entry])(1, b'entry'))
        self.assertFalse(parse([entry, entry])(1, b'entry'))
        self.assertFalse(parse([entry])(1, None))
        self.assertFalse(parse([{'source': image}])(1, None))

    def test_trim_output_rejects_ambiguous_mount_names(self):
        self.assertEqual(candidate._outputs(b'/one: 0 B trimmed\n'), {'/one'})
        with self.assertRaises(ValueError):
            candidate._outputs(b'/one:two: 0 B trimmed\n')

    def test_trim_rejects_same_device_even_when_path_differs(self):
        with self.assertRaisesRegex(ValueError, 'device_selected'):
            candidate._not_on_device(lambda *_a, **_k: b'abc\n', '/usr/bin/stat', {'/alias'}, 0xabc)
        with self.assertRaisesRegex(ValueError, 'device_unverified'):
            candidate._not_on_device(lambda *_a, **_k: b'unknown\n', '/usr/bin/stat', {'/alias'}, 0xabc)

    def test_srv_bind_mount_alias_blocks(self):
        base = {'mount': '/', 'root': '/', 'device': '8:1'}
        unrelated = {'mount': '/other', 'root': '/home', 'device': '8:1'}
        alias = {'mount': '/srv-alias', 'root': '/srv', 'device': '8:1'}
        candidate._require_no_host_alias(Path('/srv'), [base, unrelated])
        with self.assertRaisesRegex(ValueError, 'alias'):
            candidate._require_no_host_alias(Path('/srv'), [base, alias])

    def test_distinct_device_image_parent_blocks(self):
        candidate._require_parent_device((100, 3), (100, 2))
        with self.assertRaisesRegex(ValueError, 'parent_device'):
            candidate._require_parent_device((101, 3), (100, 2))

    def test_same_device_bind_mount_at_image_parent_blocks(self):
        parent = Path('/srv/modelfc-bounded-images')
        entries = [{'mount': '/', 'root': '/', 'device': '8:1'},
                   {'mount': str(parent), 'root': '/other', 'device': '8:1'}]
        with self.assertRaisesRegex(ValueError, 'mount_exists'):
            candidate._reject_target_mount(parent, entries)

    @unittest.skipUnless(shutil.which('findmnt'), 'findmnt unavailable')
    def test_real_unprivileged_fstab_parser_with_atomic_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / 'fstab').write_text('/dev/example /existing ext4 defaults 0 0\n')
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            self.addCleanup(os.close, fd)
            ident = 'b' * 32
            image = '/srv/modelfc-bounded-images/worker-' + ident + '.img'
            target = '/srv/modelfc-bounded-' + ident
            parser = candidate._parser(candidate.command, shutil.which('findmnt'), shutil.which('realpath'),
                                       image, target, '/dev/loop0')
            result = append_entry(fd, ident, validate=parser)
            self.assertEqual(len(result['after_sha256']), 64)
            self.assertIn('X-fstrim.notrim', (folder / 'fstab').read_text())

    @unittest.skipUnless(shutil.which('findmnt') and shutil.which('realpath'), 'util-linux tools unavailable')
    def test_real_parser_rejects_dangling_parent_aliases(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            real = folder / 'real'
            real.mkdir()
            alias = folder / 'alias'
            alias.symlink_to(real, target_is_directory=True)
            image, target = real / 'image', real / 'future-mount'
            table = folder / 'fstab'
            parser = candidate._parser(candidate.command, shutil.which('findmnt'), shutil.which('realpath'),
                                       str(image), str(target), '/dev/loop0')
            for source, mount in (('/dev/example', alias / target.name), (alias / image.name, '/existing')):
                table.write_text(f'{source} {mount} ext4 defaults 0 0\n')
                fd = os.open(table, os.O_RDONLY)
                try:
                    self.assertFalse(parser(fd, None))
                finally:
                    os.close(fd)

    def test_full_orchestration_and_uncertain_phase_stops_with_fake_runner(self):
        cases = [(None, False), (None, True)] + [(phase, False) for phase in
                 ('mkfs.ext4', 'losetup', 'fstab', 'mount', 'trim-control',
                  'trim-negative', 'trim-positive')]
        for failed_phase, parent_missing in cases:
            with self.subTest(failed_phase=failed_phase, parent_missing=parent_missing), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                srv = base / 'srv'
                parent = srv / 'modelfc-bounded-images'
                private = base / 'root'
                etc = base / 'etc'
                for directory in (srv, private, etc):
                    directory.mkdir()
                if not parent_missing:
                    parent.mkdir()
                (etc / 'fstab').write_text('/dev/example /existing ext4 defaults 0 0\n')
                loopfile = base / 'loop0'
                loopfile.write_bytes(b'')
                boot = base / 'boot-id'
                boot.write_text('synthetic-boot')
                mountinfo = base / 'mountinfo'
                mountinfo.write_text('synthetic')
                ident = 'c' * 32
                image = parent / ('worker-' + ident + '.img')
                mountpoint = srv / ('modelfc-bounded-' + ident)
                device = os.stat(srv).st_dev
                devtext = f'{os.major(device)}:{os.minor(device)}'
                sysdev = base / 'sysdev'
                sysdev.mkdir()
                (sysdev / devtext).symlink_to(loopfile)
                calls = []
                real_path = Path

                def mapped_path(value):
                    value = str(value)
                    paths = {'/srv': srv, '/srv/modelfc-bounded-images': parent, '/root': private,
                             '/etc': etc, '/proc/self/mountinfo': mountinfo,
                             '/sys/dev/block': sysdev,
                             '/proc/sys/kernel/random/boot_id': boot}
                    return paths.get(value, real_path(value))

                class Proxy:
                    def __getattr__(self, key):
                        return getattr(os, key)

                proxy = Proxy()
                proxy.getuid = lambda: 0
                proxy.geteuid = lambda: 0

                def fake_open(path, *args, **kwargs):
                    mapping = {'/srv': srv, '/srv/modelfc-bounded-images': parent,
                               '/root': private, '/etc': etc, '/dev/loop0': loopfile}
                    return os.open(mapping.get(str(path), path), *args, **kwargs)

                def fake_stat(path, *args, **kwargs):
                    mapping = {'/srv': srv, '/etc': etc, '/etc/fstab': etc / 'fstab', '/dev/loop0': loopfile}
                    info = os.stat(mapping.get(str(path), path), *args, **kwargs)
                    if str(path) == '/dev/loop0':
                        return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_rdev=device)
                    return info

                def fake_fstat(fd):
                    info = os.fstat(fd)
                    target = os.readlink('/proc/self/fd/' + str(fd))
                    if (target in (str(image), str(parent), str(etc / 'fstab'))
                            or target.endswith(('.allocated.json', '.intent.json', '.unresolved.json'))):
                        class RootStat:
                            st_uid = 0
                            def __getattr__(self, key):
                                return getattr(info, key)
                        return RootStat()
                    return info

                proxy.open = fake_open
                proxy.stat = fake_stat
                proxy.fstat = fake_fstat
                proxy.fstatvfs = lambda *_: SimpleNamespace(f_files=32768, f_blocks=200000, f_frsize=4096)

                def create(parent_fd, name, *, parent_pin, lock_pin):
                    calls.append('allocate')
                    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600, dir_fd=parent_fd)
                    os.ftruncate(fd, 4096)
                    info = os.fstat(fd)
                    os.close(fd)
                    _write_exclusive(parent_fd, '.production-image-stage.unresolved.json',
                                     {'version': 1, 'state': 'unresolved', 'image': name, 'parent': list(parent_pin)})
                    _write_exclusive(parent_fd, name + '.intent.json',
                                     {'version': 1, 'state': 'allocation_intent', 'image': name,
                                      'parent': list(parent_pin), 'bridge_lock': list(lock_pin), 'image_bytes': 4096})
                    return _write_exclusive(parent_fd, name + '.allocated.json',
                                            {'version': 1, 'state': 'allocation_observed_capacity_pending',
                                             'image': name, 'parent': list(parent_pin), 'bridge_lock': list(lock_pin),
                                             'image_identity': [info.st_dev, info.st_ino], 'image_bytes': 4096})

                def runner(argv, **_kwargs):
                    tool = Path(argv[0]).name
                    calls.append(tool)
                    if tool == 'realpath':
                        return (argv[-1] + '\n').encode()
                    if tool == 'stat':
                        return b'0\n'
                    if tool == failed_phase:
                        raise candidate.OperationUncertain('synthetic timeout')
                    if tool == 'fstrim':
                        if argv[-1].startswith('/proc/self/fd/'):
                            if failed_phase == 'trim-control':
                                raise candidate.OperationUncertain('synthetic timeout')
                            if failed_phase == 'trim-positive':
                                return b'/existing: 0 B trimmed\n'
                            return (str(mountpoint) + ': 0 B trimmed\n').encode()
                        if failed_phase == 'trim-negative' and calls.count('fstrim') > 1:
                            return (str(mountpoint) + ': 0 B trimmed\n').encode()
                        return b'/existing: 0 B trimmed\n'
                    return b''

                def fake_fstab(_fd, _ident, *, validate, expected_before_sha256):
                    calls.append('fstab')
                    if failed_phase == 'fstab':
                        raise candidate.OperationUncertain('synthetic timeout')
                    return {'before_sha256': expected_before_sha256,
                            'after_sha256': hashlib.sha256((etc / 'fstab').read_bytes()).hexdigest()}

                class Reader:
                    def __init__(self, *_args):
                        pass
                    def backing_identity(self, _device):
                        info = image.stat()
                        return info.st_dev, info.st_ino

                with ExitStack() as stack:
                    for key, value in {'os': proxy, 'Path': mapped_path, '_tool': lambda p: p,
                                       'protected_dir': lambda *_: None,
                                       'protected_bytes': lambda *_a, **_k: b'{}',
                                       'hold_bridge_lock': lambda *_: nullcontext(),
                                       'require_bridge_paused': lambda: None,
                                       'require_no_worker_units': lambda: None,
                                       'require_no_worker_processes': lambda *_: None,
                                       '_trim_service': lambda *_: None, '_no_target_mount': lambda *_: None,
                                       '_require_manager_mount_view': lambda: None,
                                       '_require_no_host_alias': lambda *_: None,
                                       '_parser': lambda *_: lambda *_args: True,
                                       '_loop_unbound': lambda *_: device, '_space': lambda *_: None,
                                       '_allocated_extents': lambda *_: None, 'IMAGE_BYTES': 4096,
                                       'create_image': create, 'LinuxStorageReader': Reader,
                                       'append_entry': fake_fstab,
                                       'parse_mountinfo': lambda *_: [{'mount': str(mountpoint), 'device': devtext,
                                           'fstype': 'ext4', 'root': '/', 'options': ['rw', 'nosuid', 'nodev'],
                                           'super_options': []}],
                                       'admit': lambda *_a, **_k: {'mount_id': 12}}.items():
                        stack.enter_context(patch.object(candidate, key, value))
                    stack.enter_context(patch.object(candidate.pwd, 'getpwnam', return_value=SimpleNamespace(
                        pw_uid=1000, pw_gid=1000, pw_dir='/home/zeno-worker')))
                    srv_info, lock = srv.stat(), (1, 2)
                    parent_pin = (0, 0) if parent_missing else (parent.stat().st_dev, parent.stat().st_ino)
                    args = (ident, 1000, 1000, parent_pin,
                            (srv_info.st_dev, srv_info.st_ino), lock, '/dev/loop0', 'fixed')
                    if failed_phase:
                        error_type = ValueError if failed_phase in ('trim-negative', 'trim-positive') else candidate.OperationUncertain
                        with self.assertRaises(error_type):
                            candidate.prepare(*args, runner=runner)
                    else:
                        self.assertEqual(candidate.prepare(*args, runner=runner)['status'], 'prepared_only')
                evidence = private / ('modelfc-storage-' + ident)
                if parent_missing:
                    self.assertTrue((evidence / '00-parent-observed.json').exists())
                self.assertTrue((evidence / '02-format-intent.json').exists())
                if failed_phase:
                    self.assertFalse((evidence / '08-terminal-observation.json').exists())
                    phase_intent = {'mkfs.ext4': '02-format', 'losetup': '03-attach',
                                    'fstab': '05-fstab', 'mount': '06-mount'}.get(failed_phase)
                    if phase_intent:
                        self.assertTrue((evidence / (phase_intent + '-intent.json')).exists())
                        self.assertFalse((evidence / (phase_intent + '-observed.json')).exists())
                    if failed_phase in ('mkfs.ext4', 'losetup', 'fstab', 'mount'):
                        self.assertEqual(calls.count(failed_phase), 1)
                    self.assertEqual((etc / 'fstab').read_text(), '/dev/example /existing ext4 defaults 0 0\n')
                else:
                    self.assertLess(calls.index('mkfs.ext4'), calls.index('losetup'))
                    self.assertLess(calls.index('losetup'), calls.index('fstab'))
                    self.assertLess(calls.index('fstab'), calls.index('mount'))
                    self.assertTrue((evidence / '08-terminal-observation.json').exists())


if __name__ == '__main__':
    unittest.main()
