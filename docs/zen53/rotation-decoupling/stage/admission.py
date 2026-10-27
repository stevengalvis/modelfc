"""Prospective evidence-only admission and bounded child adapters.

No action occurs on import. This must run only inside a separately approved,
non-delegated cgroup-v2 scope. The installed scope's actual values, production
state and lock identities are measured each time; absent values block capture.
"""
import fcntl
import hashlib
import json
import os
import pathlib
import pwd
import re
import resource
import selectors
import signal
import stat
import subprocess
import time

LOCKS = (("/var/lib/modelfc-deploy/deploy.lock", 2049, 543436, 1001, 1001, 0o644),
         ("/var/lib/modelfc-validator/runs/validation.lock", 2049, 535594, 1000, 1000, 0o600))
PENDING = ("/var/lib/modelfc-deploy/service-pending.json",
           "/var/lib/modelfc-deploy/test-request.json",
           "/run/modelfc-acquisition/request.json")
PREFIXES = (b"/opt/modelfc-deploy/", b"/opt/modelfc-validator/",
            b"/usr/lib/modelfc-suite/", b"/usr/lib/modelfc-suite-diag600/")
WORKERS = ("deploy", "postmerge", "acquisition", "depend", "suite", "validator", "handoff")
COOPERATIVE_LOCK_POLICY = 'EXPLICIT_TRUSTED_SERVICE_OWNER_REPLACEMENT_RISK'
# These sampled directory identities are a candidate contract, not a grant of
# immutability. Their owners can rename entries. Any drift blocks admission.
COOPERATIVE_ANCESTORS = {
    '/var/lib/modelfc-deploy': (2049, 542281, 1001, 1001, 0o700),
    '/var/lib/modelfc-validator/runs': (2049, 516735, 1000, 1000, 0o700),
}
COOPERATIVE_CONTROLLERS = {
    '/opt/modelfc-deploy/deploy_main.py':
        '26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c',
    '/opt/modelfc-validator/validate_pr.py':
        '22e417003c94fe2c3ceb30ae66a6979e63396ca7a411d93589a3fd8442be9227',
    '/opt/modelfc-validator/validate_offline_ssh.py':
        'cc70e53486ce1041cf1d12670a6df82da51bc7ee872865bbc45b5d78933503b3',
}
SCOPE_WALL_US = 360_000_000
SCOPE_STOP_US = 3_000_000
PRODUCTION_MARGIN_US = 90_000_000
MEMORY_MAX = 384 * 1024 * 1024
IO_READ_MAX = 32 * 1024 * 1024
IO_WRITE_MAX = 1024 * 1024
SCOPE_PATTERN = re.compile(r"/system.slice/zen53-owner-capture-[0-9a-f]{32}\.scope\Z")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def cooperative_sources_verified():
    """Bounded no-follow check of the actually installed controller sources."""
    import evidence
    for path, expected in COOPERATIVE_CONTROLLERS.items():
        parent, name = os.path.split(path)
        directory = evidence._open_dir(parent)
        try:
            # Root-owned installed controller files may sit on relatime.
            # Treat an unavailable O_NOATIME open as a blocked preflight.
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW |
                         os.O_NOATIME | os.O_CLOEXEC, dir_fd=directory)
            try:
                before = os.fstat(fd)
                need(stat.S_ISREG(before.st_mode) and before.st_uid ==
                     before.st_gid == 0 and before.st_nlink == 1 and
                     stat.S_IMODE(before.st_mode) == 0o555 and
                     before.st_size <= 262144, 'ADMISSION_CONTROLLER_META')
                data = bytearray()
                while len(data) <= 262144:
                    chunk = os.read(fd, min(65536, 262145-len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                after = os.fstat(fd)
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                                      s.st_mode, s.st_size, s.st_nlink,
                                      s.st_mtime_ns, s.st_ctime_ns)
                need(len(data) == before.st_size and len(data) <= 262144 and
                     identity(before) == identity(after) == identity(named) and
                     hashlib.sha256(data).hexdigest() == expected,
                     'ADMISSION_CONTROLLER_PIN')
            finally:
                os.close(fd)
        finally:
            os.close(directory)
    return True


def need(value, code):
    if not value:
        raise ValueError(code)


def command(args):
    proc = subprocess.Popen(args, env=ENV, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            close_fds=True, start_new_session=True)
    chunks = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    try:
        for pipe in chunks:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)
        deadline = time.monotonic() + 8
        while selector.get_map():
            need(time.monotonic() < deadline, "ADMISSION_QUERY_TIMEOUT")
            for key, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                part = os.read(key.fileobj.fileno(), 65536)
                if not part:
                    selector.unregister(key.fileobj)
                else:
                    target = chunks[key.fileobj]
                    need(len(target) + len(part) <= 262144, "ADMISSION_QUERY_OUTPUT")
                    target.extend(part)
        # WNOWAIT preserves the leader PID/PGID until the group is killed in
        # finally. Reaping first would permit PGID reuse before killpg.
        while True:
            need(time.monotonic() < deadline, "ADMISSION_QUERY_TIMEOUT")
            info = os.waitid(os.P_PID, proc.pid,
                             os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None:
                need(info.si_code == os.CLD_EXITED and info.si_status == 0,
                     "ADMISSION_QUERY_STATUS")
                break
            time.sleep(0.01)
        return bytes(chunks[proc.stdout]).decode("ascii")
    finally:
        kill_error = None
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            kill_error = exc
        try:
            proc.wait(timeout=2)
        finally:
            selector.close()
            proc.stdout.close()
            proc.stderr.close()
        if kill_error is not None:
            raise ValueError('ADMISSION_QUERY_CLEANUP_UNCERTAIN') from kill_error


class HostReader:
    """Read-only systemd/proc/cgroup adapter; synthetic tests replace it."""
    command = staticmethod(command)

    @staticmethod
    def read(path, limit=262144):
        with open(path, "rb", buffering=0) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) <= limit, "ADMISSION_READ_BOUND")
        return raw.decode("ascii")

    @staticmethod
    def listdir(path):
        return os.listdir(path)

    @staticmethod
    def lexists(path):
        return os.path.lexists(path)

    @staticmethod
    def missing(path):
        """Only an absent final name counts; denied or substituted parents fail."""
        parts = pathlib.PurePosixPath(path).parts
        need(parts and parts[0] == '/' and len(parts) > 1,
             'ADMISSION_ABSENCE_PATH')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY |
                     os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in parts[1:-1]:
                try:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY |
                                      os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                except FileNotFoundError:
                    return True
                os.close(fd)
                fd = next_fd
                info = os.fstat(fd)
                need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                     not info.st_mode & 0o022, 'ADMISSION_ABSENCE_PARENT')
            try:
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return True
            need(not stat.S_ISLNK(info.st_mode), 'ADMISSION_ABSENCE_ALIAS')
            return False
        finally:
            os.close(fd)

    @staticmethod
    def walk(path):
        return os.walk(path)

    def headroom(self, stage):
        rows = self.read('/proc/meminfo', 16384).splitlines()
        values = dict(line.split(':', 1) for line in rows if ':' in line)
        raw = values.get('MemAvailable', '').strip().split()
        need(len(raw) == 2 and raw[0].isdecimal() and raw[1] == 'kB' and
             int(raw[0]) >= 1536 * 1024 and os.getloadavg()[0] < 1.5,
             'ADMISSION_HEADROOM')
        disk = os.statvfs(stage)
        need(disk.f_bavail * disk.f_frsize >= 2 * 1024**3,
             'ADMISSION_DISK_HEADROOM')
        return True


def _property(reader, unit, keys):
    output = reader.command(["systemctl", "show", unit, "--no-pager",
                             *("-p" + key for key in keys)])
    rows = [line.split("=", 1) for line in output.splitlines()]
    need(len(rows) == len(keys) and {row[0] for row in rows} == set(keys)
         and all(len(row) == 2 for row in rows), "ADMISSION_PROPERTY_SCHEMA")
    return dict(rows)


def _inactive_deploy_manager_idle(reader, within_budget):
    """Prove sampled inactivity without contacting UID 1001's user bus.

    This relies on the existing cooperating-service-owner assumption. It is
    rechecked before children and at finalization, not a freeze against login
    or privileged manager activation between samples.
    """
    uid = 1001
    need(pwd.getpwnam('modelfc-deploy').pw_uid == uid,
         'ADMISSION_USER_IDENTITY')
    keys = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID',
            'NFileDescriptorStore', 'ControlGroup', 'TriggeredBy')
    expected = {'LoadState': 'loaded', 'ActiveState': 'inactive',
                'SubState': 'dead', 'MainPID': '0', 'ControlPID': '0',
                'NFileDescriptorStore': '0', 'ControlGroup': '',
                'TriggeredBy': ''}
    for unit in ('user@1001.service', 'user-runtime-dir@1001.service'):
        within_budget()
        need(_property(reader, unit, keys) == expected,
             'ADMISSION_INACTIVE_USER_UNIT')
    # System logind observations are read-only and cannot start the user bus.
    users = _bus_json(reader, ['call', 'org.freedesktop.login1',
        '/org/freedesktop/login1', 'org.freedesktop.login1.Manager',
        'ListUsers'], 'a(uso)')
    sessions = _bus_json(reader, ['call', 'org.freedesktop.login1',
        '/org/freedesktop/login1', 'org.freedesktop.login1.Manager',
        'ListSessions'], 'a(susso)')
    within_budget()
    need(type(users) is list and len(users) == 1 and
         type(users[0]) is list and len(users[0]) <= 256 and
         all(type(row) is list and len(row) == 3 and
             type(row[0]) is int and type(row[1]) is str and
             type(row[2]) is str and row[0] != uid for row in users[0]),
         'ADMISSION_INACTIVE_USER_LOGIND')
    need(type(sessions) is list and len(sessions) == 1 and
         type(sessions[0]) is list and len(sessions[0]) <= 256 and
         all(type(row) is list and len(row) == 5 and
             type(row[0]) is str and type(row[1]) is int and
             all(type(value) is str for value in row[2:]) and
             row[1] != uid for row in sessions[0]),
         'ADMISSION_INACTIVE_USER_LOGIND')
    for path in ('/var/lib/systemd/linger/modelfc-deploy',
                 '/run/user/1001'):
        within_budget()
        need(reader.missing(path), 'ADMISSION_INACTIVE_USER_TRIGGER')
    slice_path = '/sys/fs/cgroup/user.slice/user-1001.slice'
    if not reader.missing(slice_path):
        within_budget()
        rows = [line.split() for line in
                reader.read(slice_path + '/cgroup.events', 256).splitlines()]
        need(len(rows) == 2 and all(len(row) == 2 for row in rows) and
             dict(rows) == {'populated': '0', 'frozen': '0'} and
             not reader.read(slice_path + '/cgroup.procs', 8192).strip(),
             'ADMISSION_INACTIVE_USER_CGROUP')
    pids = reader.listdir('/proc')
    need(len(pids) <= 32768, 'ADMISSION_INACTIVE_USER_PROC_BOUND')
    for pid in pids:
        if not pid.isdecimal():
            continue
        within_budget()
        status = reader.read('/proc/' + pid + '/status', 8192)
        uid_rows = [line.split() for line in status.splitlines()
                    if line.startswith('Uid:')]
        need(len(uid_rows) == 1 and len(uid_rows[0]) == 5 and
             all(value.isdecimal() for value in uid_rows[0][1:]),
             'ADMISSION_INACTIVE_USER_PROC_SCHEMA')
        need(all(int(value) != uid for value in uid_rows[0][1:]),
             'ADMISSION_INACTIVE_USER_PROCESS')
    # Catch a start queued during this bounded snapshot.
    jobs = _bus_json(reader, ['call', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
        'ListJobs'], 'a(usssoo)')
    within_budget()
    need(jobs == [[]], 'ADMISSION_INACTIVE_USER_JOBS')
    need(_property(reader, 'user@1001.service', keys) == expected,
         'ADMISSION_INACTIVE_USER_CHANGED')


def _user_idle(reader, clock=time.monotonic):
    """Require both relevant user managers to show no validation work.

    The installed validator creates transient user services whose ExecStopPost
    cleanup is outside validation.lock. A missing/inaccessible manager blocks.
    """
    deadline = clock() + 16
    seen = 0
    seen_names = set()
    def within_budget():
        need(clock() < deadline, 'ADMISSION_USER_QUERY_DEADLINE')
    for account in ('modelfc-validator', 'modelfc-deploy'):
        within_budget()
        if account == 'modelfc-deploy':
            state = _property(reader, 'user@1001.service',
                              ('ActiveState', 'MainPID'))
            within_budget()
            if state == {'ActiveState': 'inactive', 'MainPID': '0'}:
                _inactive_deploy_manager_idle(reader, within_budget)
                continue
            need(state['ActiveState'] == 'active' and
                 state['MainPID'].isdecimal() and int(state['MainPID']) > 0,
                 'ADMISSION_USER_MANAGER_STATE')
        prefix = ['systemctl', '--user', '--machine=' + account + '@.host']
        jobs = reader.command([*prefix, 'list-jobs', '--no-legend', '--no-pager'])
        within_budget()
        need(jobs.strip() == '', 'ADMISSION_USER_JOBS')
        units = reader.command([*prefix, 'list-units', '--all',
                                '--type=service', '--plain', '--no-legend',
                                '--no-pager'])
        within_budget()
        lines = units.splitlines()
        need(len(lines) <= 256, 'ADMISSION_USER_UNIT_BOUND')
        for line in lines:
            within_budget()
            fields = line.split()
            need(fields and re.fullmatch(r'[A-Za-z0-9_.@\\-]+\.service', fields[0])
                 and (account, fields[0]) not in seen_names,
                 'ADMISSION_USER_UNITS')
            unit = fields[0]
            seen_names.add((account, unit))
            if not unit.startswith('modelfc-'):
                continue
            seen += 1
            need(seen <= 32, 'ADMISSION_USER_UNIT_BOUND')
            keys = ('ActiveState', 'MainPID', 'ControlPID',
                    'NFileDescriptorStore', 'ControlGroup')
            output = reader.command([*prefix, 'show', unit, '--no-pager',
                                     *('-p' + key for key in keys)])
            within_budget()
            rows = [row.split('=', 1) for row in output.splitlines()]
            need(len(rows) == len(keys) and all(len(row) == 2 for row in rows)
                 and {row[0] for row in rows} == set(keys),
                 'ADMISSION_USER_UNIT_SCHEMA')
            props = dict(rows)
            need(props['ActiveState'] == 'inactive' and
                 all(props[key] == '0' for key in
                     ('MainPID', 'ControlPID', 'NFileDescriptorStore')) and
                 props['ControlGroup'] == '', 'ADMISSION_USER_WORKER')


def _bus_json(reader, args, expected_type):
    data = json.loads(reader.command(["busctl", "--json=short", *args]))
    need(type(data) is dict and data.get("type") == expected_type,
         "ADMISSION_BUS_SCHEMA")
    return data.get("data")


def _idle(reader, required_us, monotonic_us, realtime_us):
    jobs = _bus_json(reader, ["call", "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "ListJobs"], "a(usssoo)")
    need(jobs == [[]], "ADMISSION_JOBS")
    need(all(not reader.lexists(path) for path in PENDING), "ADMISSION_PENDING")
    _user_idle(reader)
    system_deadline = time.monotonic() + 16
    def system_budget():
        need(time.monotonic() < system_deadline, 'ADMISSION_SYSTEM_QUERY_DEADLINE')
    units = reader.command(["systemctl", "list-units", "--all", "--type=service",
                            "--plain", "--no-legend", "--no-pager"])
    system_budget()
    lines = units.splitlines()
    need(len(lines) <= 256, 'ADMISSION_SYSTEM_UNIT_BOUND')
    seen_units = set()
    matches = 0
    for line in lines:
        system_budget()
        fields = line.split()
        need(fields and re.fullmatch(r'[A-Za-z0-9_.@\\-]+\.service', fields[0]) and
             fields[0] not in seen_units, 'ADMISSION_SYSTEM_UNIT_SCHEMA')
        name = fields[0]
        seen_units.add(name)
        if not name.startswith("modelfc-") or name == "modelfc-corner-api.service":
            continue
        matches += 1
        need(matches <= 32, 'ADMISSION_SYSTEM_UNIT_BOUND')
        props = _property(reader, name, ("MainPID", "ControlPID", "NFileDescriptorStore", "ControlGroup"))
        system_budget()
        need(all(props[key] == "0" for key in ("MainPID", "ControlPID", "NFileDescriptorStore")),
             "ADMISSION_WORKER")
        group = props["ControlGroup"]
        need(not group or (group.startswith("/system.slice/") and ".." not in group),
             "ADMISSION_WORKER_CGROUP")
        if group:
            for root, dirs, files in reader.walk("/sys/fs/cgroup" + group):
                need(len(root) < 512 and len(dirs) <= 64, "ADMISSION_CGROUP_WALK")
                if "cgroup.procs" in files:
                    need(not reader.read(root + "/cgroup.procs", 8192).strip(),
                         "ADMISSION_WORKER_DESCENDANT")
    for entry in reader.listdir("/proc"):
        if not entry.isdecimal():
            continue
        try:
            args = reader.read("/proc/" + entry + "/cmdline", 16384).encode("ascii").split(b"\0")
        except FileNotFoundError:
            continue
        need(not any(arg.startswith(PREFIXES) and arg.endswith(b".py") for arg in args),
             "ADMISSION_CONTROLLER_BUSY")
    timers = reader.command(["systemctl", "list-units", "--type=timer",
                             "--state=active", "--plain", "--no-legend", "--no-pager"])
    system_budget()
    timer_lines = timers.splitlines()
    need(len(timer_lines) <= 256, 'ADMISSION_TIMER_BOUND')
    seen_timers = set()
    relevant_timers = 0
    for line in timer_lines:
        system_budget()
        parts = line.split()
        need(parts and re.fullmatch(r'[A-Za-z0-9_.@\-]+\.timer', parts[0]) and
             parts[0] not in seen_timers, 'ADMISSION_TIMER_SCHEMA')
        name = parts[0]
        seen_timers.add(name)
        if not name.startswith("modelfc-"):
            continue
        relevant_timers += 1
        need(relevant_timers <= 32, 'ADMISSION_TIMER_BOUND')
        obj = _bus_json(reader, ["call", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
            "GetUnit", "s", name], "o")
        system_budget()
        need(type(obj) is list and len(obj) == 1 and type(obj[0]) is str and
             obj[0].startswith("/org/freedesktop/systemd1/unit/"), "ADMISSION_TIMER_OBJECT")
        for key, now in (("NextElapseUSecMonotonic", monotonic_us),
                         ("NextElapseUSecRealtime", realtime_us)):
            value = _bus_json(reader, ["get-property", "org.freedesktop.systemd1",
                obj[0], "org.freedesktop.systemd1.Timer", key], "t")
            system_budget()
            need(type(value) is int and 0 <= value < 2**64, "ADMISSION_TIMER_SCHEMA")
            need(value in (0, 2**64 - 1) or value - now > required_us,
                 "ADMISSION_TIMER_NEAR")
    api = _property(reader, "modelfc-corner-api.service",
                    ("ActiveState", "MainPID", "InvocationID",
                     "RuntimeMaxUSec", "ExecMainStartTimestampMonotonic"))
    system_budget()
    need(api["ActiveState"] == "active" and api["MainPID"].isdecimal() and
         int(api["MainPID"]) > 0 and
         re.fullmatch(r"[0-9a-f]{32}", api["InvocationID"]) and
         api["RuntimeMaxUSec"] == "30min" and
         api["ExecMainStartTimestampMonotonic"].isdecimal(),
         "ADMISSION_API_STATE")
    age = monotonic_us - int(api["ExecMainStartTimestampMonotonic"])
    # The diagnostic does not call the API. Its bounded cgroup, resource and
    # independent job/lock/timer checks remain in force across API restarts.
    # Validate this observation, but do not require the API invocation to
    # outlive the independent diagnostic's worst-case scope lifetime.
    need(0 <= age < 1_800_000_000, "ADMISSION_API_STATE")


def _same_scope(reader, scope):
    need(SCOPE_PATTERN.fullmatch(scope) is not None, "ADMISSION_SCOPE_NAME")
    need(reader.read("/proc/self/cgroup", 4096) == "0::" + scope + "\n",
         "ADMISSION_SCOPE_IDENTITY")
    base = "/sys/fs/cgroup" + scope
    need(reader.read(base + "/cgroup.type", 128).strip() == "domain" and
         reader.read(base + "/cgroup.subtree_control", 128).strip() == "" and
         str(os.getpid()) in reader.read(base + "/cgroup.procs", 8192).split(),
         "ADMISSION_SCOPE_TYPE")
    unit = scope.rsplit("/", 1)[1]
    props = _property(reader, unit, ("ActiveState", "ControlGroup", "Delegate",
                                     "RuntimeMaxUSec", "KillMode",
                                     "ActiveEnterTimestampMonotonic",
                                     "RuntimeRandomizedExtraUSec",
                                     "TimeoutStopUSec", "SendSIGKILL",
                                     "FinalKillSignal"))
    need(props == {"ActiveState": "active", "ControlGroup": scope,
                   "Delegate": "no", "RuntimeMaxUSec": "6min",
                   "KillMode": "control-group", "RuntimeRandomizedExtraUSec": "0",
                   "TimeoutStopUSec": "3s", "SendSIGKILL": "yes",
                   "FinalKillSignal": "9",
                   "ActiveEnterTimestampMonotonic":
                       props.get("ActiveEnterTimestampMonotonic")},
         "ADMISSION_SCOPE_PROPERTIES")
    started = props["ActiveEnterTimestampMonotonic"]
    need(started.isdecimal() and int(started) > 0,
         "ADMISSION_SCOPE_START")
    def number(name, cap):
        raw = reader.read(base + "/" + name, 128).strip()
        need(raw.isdecimal() and 0 < int(raw) <= cap, "ADMISSION_" + name.upper().replace('.', '_'))
    number("memory.max", MEMORY_MAX)
    need(reader.read(base + "/memory.swap.max", 128).strip() == "0", "ADMISSION_SWAP")
    number("pids.max", 32)
    cpu = reader.read(base + "/cpu.max", 128).strip().split()
    need(len(cpu) == 2 and all(x.isdecimal() for x in cpu) and
         0 < int(cpu[0]) * 2 <= int(cpu[1]), "ADMISSION_CPU")
    return base, int(started) + SCOPE_WALL_US + SCOPE_STOP_US


def _io_limit(reader, base, devices):
    need(type(devices) is tuple and devices and all(
        type(x) is str and re.fullmatch(r"[0-9]+:[0-9]+", x) for x in devices),
         "ADMISSION_IO_DEVICES")
    need(len(set(devices)) == len(devices), "ADMISSION_IO_DEVICES")
    lines = reader.read(base + "/io.max", 4096).splitlines()
    found = {}
    for line in lines:
        fields = line.split()
        need(len(fields) == 5 and re.fullmatch(r"[0-9]+:[0-9]+", fields[0])
             and fields[0] not in found, "ADMISSION_IO_SCHEMA")
        pairs = [item.split("=", 1) for item in fields[1:]]
        need(all(len(pair) == 2 for pair in pairs), "ADMISSION_IO_SCHEMA")
        limits = dict(pairs)
        need(len(limits) == 4 and set(limits) ==
             {"rbps", "wbps", "riops", "wiops"}, "ADMISSION_IO_SCHEMA")
        found[fields[0]] = limits
    need(set(devices) <= set(found), "ADMISSION_IO_MISSING")
    need(set(found) == set(devices), "ADMISSION_IO_EXTRA")
    for device in devices:
        limits = found[device]
        for key, cap in (("rbps", IO_READ_MAX), ("wbps", IO_WRITE_MAX)):
            value = limits[key]
            need(value.isdecimal() and 0 < int(value) <= cap, "ADMISSION_IO_LIMIT")
        need(all(limits[key].isdecimal() and 0 < int(limits[key]) <= 1024
                 for key in ("riops", "wiops")), "ADMISSION_IO_OPS")


class Admission:
    def __init__(self, scope, devices, *, backing_recheck=None,
                 reader=None, monotonic=time.monotonic_ns,
                 realtime=time.time_ns, locks=LOCKS, cooperative_policy=None):
        self.scope, self.devices = scope, devices
        self.backing_recheck = backing_recheck
        self.reader = reader if reader is not None else HostReader()
        self.monotonic, self.realtime, self.locks = monotonic, realtime, locks
        self.fds = []
        self.started = None
        self.checks = 0
        self.unresolved = []
        self.anchors = []
        self.cooperative_policy = cooperative_policy

    def _cooperative_sources(self):
        if self.cooperative_policy is not None:
            need(self.cooperative_policy == COOPERATIVE_LOCK_POLICY,
                 'ADMISSION_COOPERATIVE_POLICY')
            need(cooperative_sources_verified(), 'ADMISSION_CONTROLLER_PIN')

    def __call__(self):
        return self

    def __enter__(self):
        need(os.geteuid() == 0, "ADMISSION_ROOT")
        try:
            self._check_backing()
            # Never acquire production locks before proving this process is
            # inside the independently bounded scope. Otherwise an erroneous
            # out-of-scope invocation could enter the protective hold forever.
            base, _ = _same_scope(self.reader, self.scope)
            _io_limit(self.reader, base, self.devices)
            self._cooperative_sources()
            for path, device, inode, uid, gid, mode in self.locks:
                parent = pathlib.Path(path).parent
                parents = []
                for ancestor in (parent, *parent.parents):
                    info = os.lstat(ancestor)
                    observed = (info.st_dev, info.st_ino, info.st_uid,
                                info.st_gid, stat.S_IMODE(info.st_mode))
                    root_protected = info.st_uid == 0 and not info.st_mode & 0o022
                    cooperative = (self.cooperative_policy == COOPERATIVE_LOCK_POLICY and
                                   COOPERATIVE_ANCESTORS.get(str(ancestor)) == observed)
                    need(stat.S_ISDIR(info.st_mode) and (root_protected or cooperative),
                         "ADMISSION_LOCK_ANCESTOR")
                    parents.append((str(ancestor), info.st_dev, info.st_ino,
                                    info.st_uid, info.st_gid, info.st_mode))
                before = os.lstat(path)
                expected = (device, inode, uid, gid, mode)
                def ident(st):
                    return (st.st_dev, st.st_ino, st.st_uid, st.st_gid,
                            stat.S_IMODE(st.st_mode))
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                     ident(before) == expected, "ADMISSION_LOCK_PIN")
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                self.fds.append(fd)
                need(ident(os.fstat(fd)) == expected and ident(os.lstat(path)) == expected,
                     "ADMISSION_LOCK_CHANGED")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.anchors.append((path, expected, tuple(parents)))
            # No child has been spawned yet. If scope identity changed during
            # lock acquisition, release these locks without protective hold.
            base, _ = _same_scope(self.reader, self.scope)
            _io_limit(self.reader, base, self.devices)
            self.started = self.monotonic()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _check_locks(self):
        need(len(self.fds) == len(self.locks) and self.started is not None,
             "ADMISSION_LOCKS_NOT_HELD")
        for fd, (path, expected, parents) in zip(self.fds, self.anchors):
            for ancestor, *identity in parents:
                info = os.lstat(ancestor)
                need((info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                      info.st_mode) == tuple(identity), "ADMISSION_LOCK_ANCESTOR_CHANGED")
            current = os.lstat(path)
            need(stat.S_ISREG(current.st_mode) and current.st_nlink == 1 and
                 (current.st_dev, current.st_ino, current.st_uid, current.st_gid,
                  stat.S_IMODE(current.st_mode)) == expected and
                 (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == expected[:2],
                 "ADMISSION_LOCK_REPLACED")

    def _check_backing(self):
        need(callable(self.backing_recheck) and self.backing_recheck(),
             'ADMISSION_BACKING_CHANGED')

    def check(self):
        self._check_backing()
        self._cooperative_sources()
        self._check_locks()
        elapsed = self.monotonic() - self.started
        need(0 <= elapsed < (90 if self.checks < 2 else 135) * 1_000_000_000,
             "ADMISSION_PREFLIGHT_TIME")
        base, scope_deadline = _same_scope(self.reader, self.scope)
        _io_limit(self.reader, base, self.devices)
        need(self.reader.headroom('/var/lib/' + self.scope.rsplit('/', 1)[1][:-6]),
             'ADMISSION_HEADROOM')
        for key, cap in ((resource.RLIMIT_NOFILE, 128),
                         (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
                         (resource.RLIMIT_CORE, 0)):
            soft, hard = resource.getrlimit(key)
            need(soft >= 0 and hard >= 0 and soft <= cap and hard <= cap,
                 "ADMISSION_RLIMIT")
        now = self.monotonic() // 1000
        need(0 < scope_deadline - now <= SCOPE_WALL_US + SCOPE_STOP_US,
             "ADMISSION_SCOPE_WALL_CLOCK")
        # Preserve 90 seconds beyond the scope's hard kill deadline, even
        # when fsync/validation or cleanup takes the entire remaining wall.
        required = scope_deadline - now + PRODUCTION_MARGIN_US
        _idle(self.reader, required, now,
              self.realtime() // 1000)
        need(0 <= self.monotonic() - self.started <
             (90 if self.checks < 2 else 135) * 1_000_000_000,
             "ADMISSION_CHECK_TIMEOUT")
        self.checks += 1
        return True

    def finalize(self):
        """Recheck cooperative identities and cleanup after evidence reload.

        Unlike the startup checks, this may follow the 180-second held-source
        bracket. It still requires live scope bounds and 90-second clearance.
        """
        self._check_backing()
        self._cooperative_sources()
        self._check_locks()
        base, scope_deadline = _same_scope(self.reader, self.scope)
        _io_limit(self.reader, base, self.devices)
        now = self.monotonic() // 1000
        need(0 < scope_deadline - now <= SCOPE_WALL_US + SCOPE_STOP_US,
             'ADMISSION_FINAL_SCOPE_TIME')
        _idle(self.reader, scope_deadline - now + PRODUCTION_MARGIN_US,
              now, self.realtime() // 1000)
        self._check_locks()
        return True

    def __exit__(self, *_):
        # A failed Popen postcheck leaves no handle with controller.run. Keep
        # the pinned production locks until the child is reaped and this scope
        # contains only this process. If that cannot be shown, hold until the
        # independently enforced six-minute systemd scope deadline kills the
        # entire group. Never release locks and announce clean completion.
        if self.started is not None:
            self._protective_hold()
        error = None
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except OSError as exc:
                error = exc
        self.fds.clear()
        self.started = None
        if error is not None:
            raise ValueError("ADMISSION_LOCK_CLOSE_UNCERTAIN") from error

    def _protective_hold(self):
        while not self._hold_once():
            try:
                time.sleep(1)
            except BaseException:
                # SIGKILL from the independently verified 360-second scope
                # cannot be caught. Signals/errors must not drop the locks.
                pass

    def _hold_once(self):
        pending = []
        for child in self.unresolved:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except BaseException:
                pass
            try:
                child.wait(timeout=2)
            except BaseException:
                pending.append(child)
        self.unresolved = pending
        try:
            members = self.reader.read('/sys/fs/cgroup' + self.scope +
                                       '/cgroup.procs', 8192).split()
        except BaseException:
            return False
        return not pending and members == [str(os.getpid())]


class BoundedSpawner:
    """Popen adapter used for both children after their exact argv is built."""
    def __init__(self, guard):
        self.guard = guard

    def __call__(self, argv, **kwargs):
        need(self.guard.started is not None and self.guard.checks >= 2 and
             len(self.guard.fds) == len(self.guard.locks), "SPAWN_GUARD")
        need(kwargs.get("stdin") in (subprocess.PIPE, subprocess.DEVNULL),
             "SPAWN_STDIN")
        need(kwargs == dict(stdin=kwargs.get("stdin"), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True,
                            start_new_session=True), "SPAWN_KWARGS")
        need(tuple(argv) and all(type(x) is str and x and "\x00" not in x for x in argv),
             "SPAWN_ARGV")
        self.guard.check()
        need(_same_scope(self.guard.reader, self.guard.scope), "SPAWN_SCOPE")
        child = subprocess.Popen(argv, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                                 **kwargs)
        try:
            need(self.guard.reader.read("/proc/" + str(child.pid) + "/cgroup", 4096)
                 == "0::" + self.guard.scope + "\n", "SPAWN_CHILD_SCOPE")
            return child
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                self.guard.unresolved.append(child)
                raise
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.guard.unresolved.append(child)
            raise
