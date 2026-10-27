"""PREPARED ONLY. Requires separate execution approval; no automatic retry.

Root supervisor provisions only fresh stage objects and examines the temporary unit's cgroup/processes. It does not
trace syscalls, read environments, read FD contents or touch production controls.
"""
import os
import sys
import json
import time
import stat
import fcntl
import hashlib
import pathlib
import subprocess
import datetime
import pwd
import select

BASE = pathlib.Path(__file__).resolve().parent
UNIT = "zen53-mincompat.service"
USER = "zen53-mincompat"
LOCKS = (("/var/lib/modelfc-deploy/deploy.lock", 543436, 1001, 1001, 0o644),
         ("/var/lib/modelfc-validator/runs/validation.lock", 535594, 1000, 1000, 0o600))
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
EVIDENCE = BASE / "evidence"
events = []
fds = []
started = False
result = {"status": "BLOCKED", "native_runtime": "UNTESTED", "transition": "UNTESTED",
          "full_coding_isolation": "UNTESTED", "provider_model_requests": 0}


# BEGIN ZEN53 provisioning protocol (no module-level operations).
PROVISION_VERSION = 1
PROVISION_WAIT_NS = 5_000_000_000
RELEASE_LEASE_NS = 1_000_000_000


def require(value, code):
    if not value:
        raise RuntimeError(code)


def object_id(info):
    return [info.st_dev, info.st_ino]


def directory_info(info, uid, mode, gid=0):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == mode, "PROVISION_DIRECTORY_METADATA")


def open_directory(parent, name):
    require(type(name) is str and name not in ("", ".", "..") and "/" not in name,
            "PROVISION_COMPONENT")
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=parent)


def bounded_read(fd, limit=4096):
    data = bytearray()
    while True:
        part = os.read(fd, min(4096, limit + 1 - len(data)))
        if not part:
            return bytes(data)
        data.extend(part)
        require(len(data) <= limit, "PROVISION_READ_BOUND")


def read_at(parent, name, limit=4096):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        return bounded_read(fd, limit)
    finally:
        os.close(fd)


def canonical_record(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def read_record(parent, name, missing=False):
    try:
        fd = os.open(name + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        if missing:
            return None
        raise
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o444 and info.st_nlink == 2,
                "PROVISION_RECORD_METADATA")
        require(object_id(os.stat("." + name + ".sealed", dir_fd=parent, follow_symlinks=False))
                == object_id(info), "PROVISION_RECORD_ALIAS")
        raw = bounded_read(fd)
        value = json.loads(raw)
        require(canonical_record(value) == raw, "PROVISION_RECORD_ENCODING")
        require(object_id(os.stat(name + ".json", dir_fd=parent, follow_symlinks=False))
                == object_id(info), "PROVISION_RECORD_REPLACED")
        return value
    finally:
        os.close(fd)


def exact_keys(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()), "PROVISION_RECORD_SCHEMA")


def valid_nonce(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def valid_identity(value):
    exact_keys(value, "pid start uid gid invocation group")
    require(all(type(value[k]) is int and 0 < value[k] < 2**32 - 1
                for k in ("pid", "uid", "gid")) and type(value["start"]) is int
            and value["start"] > 0 and type(value["invocation"]) is str
            and len(value["invocation"]) == 32
            and all(c in "0123456789abcdef" for c in value["invocation"])
            and value["group"] == "/system.slice/zen53-mincompat.service", "PROVISION_IDENTITY_SCHEMA")


def valid_attempt(value):
    exact_keys(value, "version nonce parent created_ns deadline_ns")
    require(type(value["version"]) is int and value["version"] == PROVISION_VERSION
            and valid_nonce(value["nonce"]) and type(value["parent"]) is list
            and len(value["parent"]) == 2 and all(type(n) is int and n > 0 for n in value["parent"])
            and type(value["created_ns"]) is int and value["created_ns"] > 0
            and type(value["deadline_ns"]) is int
            and value["deadline_ns"] == value["created_ns"] + PROVISION_WAIT_NS,
            "PROVISION_ATTEMPT_SCHEMA")


def parse_proc_identity(procfd, invocation, group):
    # procfd is a pinned /proc/PID directory, never a reusable pathname.
    raw = read_at(procfd, "stat").decode("ascii")
    pid = int(raw.split(" ", 1)[0])
    fields = raw.rsplit(") ", 1)[1].split()
    require(fields[0] not in ("Z", "X", "x"), "PROVISION_PROCESS_DEAD")
    status = dict(line.split(":", 1) for line in read_at(procfd, "status", 16384).decode("ascii").splitlines()
                  if ":" in line)
    uid = [int(v) for v in status["Uid"].split()]
    gid = [int(v) for v in status["Gid"].split()]
    require(len(uid) == len(gid) == 4 and len(set(uid)) == len(set(gid)) == 1,
            "PROVISION_PROCESS_CREDENTIALS")
    require(read_at(procfd, "cgroup").decode("ascii") == "0::" + group + "\n",
            "PROVISION_CGROUP")
    value = dict(pid=pid, start=int(fields[19]), uid=uid[0], gid=gid[0],
                 invocation=invocation, group=group)
    valid_identity(value)
    return value
# END ZEN53 shared protocol.


def unique_release_request(raw):
    require(type(raw) is bytes and len(raw) <= 65536, "PROVISION_LOG_BOUND")
    requests = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue  # An incomplete tail cannot authorize release.
        try:
            item = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if type(item) is dict and item.get("phase") == "release_request":
            require(canonical_record(item) == line, "PROVISION_REQUEST_ENCODING")
            requests.append(item)
    require(len(requests) <= 1, "PROVISION_DUPLICATE_REQUEST")
    return requests[0] if requests else None

class ProvisionRootIO:
    """Privileged syscall adapter; offline tests substitute every IO dependency."""
    def __init__(self, base, query, guard):
        self.base, self.query, self.guard = base, query, guard
        self.owned = []
        self.pidfd = self.procfd = self.stagefd = self.sourcefd = self.readyfd = None
        self.identity = None
        self.close_uncertain = False

    def own(self, fd):
        self.owned.append(fd)
        return fd

    def now(self):
        return time.monotonic_ns()

    def open_stage(self):
        # Walk absolute path without symlink traversal. /tmp may be sticky root;
        # every component beneath it and the stage must be protected root-owned.
        parts = str(self.base).split("/")
        require(parts[:2] == ["", "tmp"] and len(parts) == 3
                and parts[2].startswith("zen53-") and parts[2] not in (".", ".."),
                "PROVISION_STAGE_PATH")
        root = self.own(os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
        tmp = self.own(open_directory(root, "tmp"))
        directory_info(os.fstat(tmp), 0, 0o1777)
        self.stagefd = self.own(open_directory(tmp, parts[2]))
        directory_info(os.fstat(self.stagefd), 0, 0o700)
        self.sourcefd = self.own(open_directory(self.stagefd, "daemon-tmp"))
        directory_info(os.fstat(self.sourcefd), 0, 0o555)
        rootfs = self.own(open_directory(self.stagefd, "rootfs"))
        self.tmpfd, self.rootfsfd = tmp, rootfs
        self.stage_name = parts[2]
        diagnostic = self.own(open_directory(rootfs, "diagnostic"))
        self.diagfd = diagnostic
        self.readyfd = self.own(open_directory(diagnostic, "readiness"))
        for fd in (rootfs, diagnostic, self.readyfd):
            directory_info(os.fstat(fd), 0, 0o555)
        require(os.listdir(self.sourcefd) == os.listdir(self.readyfd) == [], "PROVISION_NO_REUSE")
        self.parent = object_id(os.fstat(self.sourcefd))
        self.ready_id = object_id(os.fstat(self.readyfd))
        return self.parent

    def nonce(self):
        return os.urandom(32).hex()

    def stage_check(self):
        directory_info(os.fstat(self.sourcefd), 0, 0o555)
        directory_info(os.fstat(self.readyfd), 0, 0o555)
        require(object_id(os.stat("daemon-tmp", dir_fd=self.stagefd, follow_symlinks=False))
                == self.parent, "PROVISION_SOURCE_REPLACED")
        for parent, name, fd in ((self.tmpfd, self.stage_name, self.stagefd),
                                 (self.stagefd, "rootfs", self.rootfsfd),
                                 (self.rootfsfd, "diagnostic", self.diagfd),
                                 (self.diagfd, "readiness", self.readyfd)):
            require(object_id(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    == object_id(os.fstat(fd)), "PROVISION_STAGE_REPLACED")

    def publish(self, name, value):
        require(name in ("attempt", "provision", "release"), "PROVISION_RECORD_NAME")
        self.stage_check()
        raw = canonical_record(value)
        require(len(raw) <= 4096, "PROVISION_RECORD_BOUND")
        temp = "." + name + ".sealed"
        fd = self.own(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o600, dir_fd=self.readyfd))
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and info.st_nlink == 1, "PROVISION_NEW_RECORD")
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            require(written > 0, "PROVISION_SHORT_WRITE")
            offset += written
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        self.close_one(fd)  # No writable descriptor survives publication.
        # No-replace atomic commit. Both immutable names remain as evidence;
        # exact nlink=2 is intentional, not a shared writable hardlink.
        sealed = os.stat(temp, dir_fd=self.readyfd, follow_symlinks=False)
        require(object_id(sealed) == object_id(info) and stat.S_ISREG(sealed.st_mode)
                and sealed.st_uid == sealed.st_gid == 0 and stat.S_IMODE(sealed.st_mode) == 0o444
                and sealed.st_nlink == 1, "PROVISION_SEALED_REPLACED")
        if name != "attempt":
            self.check()  # Sealing/fsync may have outlived the pinned process.
        if name == "release":
            require(self.now() < value["expires_ns"], "PROVISION_RELEASE_EXPIRED_BEFORE_COMMIT")
        os.link(temp, name + ".json", src_dir_fd=self.readyfd,
                dst_dir_fd=self.readyfd, follow_symlinks=False)
        if name != "attempt":
            self.live()  # Failure after link means publication may have committed.


    def pin(self):
        first = self.query()
        require(first.get("ActiveState") == "active" and first.get("SubState") == "running",
                "PROVISION_UNIT_NOT_RUNNING")
        pid = int(first["MainPID"])
        require(pid > 0, "PROVISION_MAINPID")
        self.pidfd = self.own(os.pidfd_open(pid, 0))
        self.live()
        self.procfd = self.own(os.open("/proc/" + str(pid), os.O_RDONLY | os.O_DIRECTORY
                                      | os.O_NOFOLLOW | os.O_CLOEXEC))
        require(os.readlink("ns/time", dir_fd=self.procfd) == os.readlink("/proc/self/ns/time"),
                "PROVISION_CLOCK_NAMESPACE")
        self.identity = parse_proc_identity(self.procfd, first["InvocationID"], first["ControlGroup"])
        require(self.identity["pid"] == pid, "PROVISION_PID_SUBSTITUTION")
        require(read_at(self.procfd, "cmdline") ==
                b"/usr/bin/python3\x00-I\x00-S\x00-B\x00/diagnostic/launch.py\x00",
                "PROVISION_LAUNCHER_COMMAND")
        self.check()
        return self.identity

    def live(self):
        require(self.pidfd is not None and not select.select([self.pidfd], [], [], 0)[0],
                "PROVISION_PIDFD_DEAD")

    def check(self):
        self.live()
        current = self.query()
        expected = self.identity
        require(current.get("MainPID") == str(expected["pid"])
                and current.get("InvocationID") == expected["invocation"]
                and current.get("ControlGroup") == expected["group"]
                and current.get("ActiveState") == "active" and current.get("SubState") == "running",
                "PROVISION_UNIT_IDENTITY_CHANGED")
        require(parse_proc_identity(self.procfd, expected["invocation"], expected["group"])
                == expected, "PROVISION_PROCESS_IDENTITY_CHANGED")
        self.live()
        self.stage_check()

    def create(self, identity):
        self.check()
        require(os.listdir(self.sourcefd) == [], "PROVISION_SOURCE_NOT_EMPTY")
        name = "codex-daemon-" + str(identity["uid"])
        os.mkdir(name, 0o700, dir_fd=self.sourcefd)  # EEXIST is fatal, never repair.
        fd = self.own(open_directory(self.sourcefd, name))
        initial = os.fstat(fd)
        directory_info(initial, 0, 0o700)
        require(os.listdir(fd) == [] and object_id(os.stat(name, dir_fd=self.sourcefd,
                follow_symlinks=False)) == object_id(initial), "PROVISION_NEW_CHILD")
        self.check()
        os.fchown(fd, identity["uid"], identity["gid"])
        self.check()
        os.fchmod(fd, 0o700)
        self.check()
        child = object_id(os.fstat(fd))
        self.childfd, self.child_id = fd, child
        self.verify_child(identity)
        return child

    def verify_child(self, identity):
        self.check()
        name = "codex-daemon-" + str(identity["uid"])
        info = os.fstat(self.childfd)
        directory_info(info, identity["uid"], 0o700, identity["gid"])
        require(info.st_gid == identity["gid"] and object_id(info) == self.child_id
                and object_id(os.stat(name, dir_fd=self.sourcefd, follow_symlinks=False)) == self.child_id
                and os.listdir(self.sourcefd) == [name] and os.listdir(self.childfd) == [],
                "PROVISION_CHILD_CHANGED")

    def close_one(self, fd):
        # A failed close may already have consumed the fd: never retry that number.
        self.owned.remove(fd)
        try:
            os.close(fd)
        except BaseException:
            self.close_uncertain = True
            raise

    def close(self):
        # Never remove/reown partial objects. Keep evidence for separate review.
        errors = []
        for fd in list(reversed(self.owned)):
            try:
                self.close_one(fd)
            except BaseException as error:
                errors.append(type(error).__name__)
        require(not errors and not self.close_uncertain, "PROVISION_DESCRIPTOR_CLEANUP")


class ProvisionTransaction:
    """Same orchestration used with real IO and the offline failure-injecting adapter."""
    def __init__(self, io):
        self.io, self.phase = io, "new"
        self.attempt = self.identity = self.provision = None

    def begin(self):
        require(self.phase == "new", "PROVISION_PHASE")
        self.phase = "opening"
        parent = self.io.open_stage()
        now = self.io.now()
        self.attempt = dict(version=1, nonce=self.io.nonce(), parent=parent,
                            created_ns=now, deadline_ns=now + PROVISION_WAIT_NS)
        valid_attempt(self.attempt)
        self.io.publish("attempt", self.attempt)
        self.phase = "waiting_process"

    def prepare(self):
        require(self.phase == "waiting_process", "PROVISION_PHASE")
        self.phase = "preparing"
        self.identity = self.io.pin()
        valid_identity(self.identity)
        require(self.io.now() < self.attempt["deadline_ns"], "PROVISION_DEADLINE")
        child = self.io.create(self.identity)
        self.io.check()
        require(self.io.now() < self.attempt["deadline_ns"], "PROVISION_DEADLINE")
        self.provision = dict(version=1, nonce=self.attempt["nonce"], identity=self.identity,
                              parent=self.attempt["parent"], child=child)
        self.io.publish("provision", self.provision)
        self.phase = "waiting_release"

    def release(self, request):
        require(self.phase == "waiting_release", "PROVISION_PHASE")
        require(request == {"phase": "release_request", "nonce": self.attempt["nonce"]},
                "PROVISION_RELEASE_REQUEST")
        self.phase = "guarding"
        self.io.verify_child(self.identity)
        eligible_until = self.io.guard()  # Absolute timer/API deadline minus 90s.
        self.io.verify_child(self.identity)
        now = self.io.now()
        require(type(eligible_until) is int and now < eligible_until
                and now < self.attempt["created_ns"] + 20_000_000_000, "PROVISION_RELEASE_DEADLINE")
        record = dict(version=1, nonce=self.attempt["nonce"], identity=self.identity,
                      provision_sha256=hashlib.sha256(canonical_record(self.provision)).hexdigest(),
                      issued_ns=now, expires_ns=min(now + RELEASE_LEASE_NS, eligible_until))
        self.phase = "publishing_release"
        self.io.publish("release", record)
        self.phase = "released"  # Atomic release commit; no automatic second release.

    def close(self):
        self.io.close()
        self.phase = "closed"

provision_transaction = None

def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        while data := stream.read(65536):
            h.update(data)
    return h.hexdigest()


def command(args):
    value = subprocess.run(args, env=ENV, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=8, close_fds=True)
    need(value.returncode == 0 and len(value.stdout) < 262144, "READ_QUERY_OR_COMMAND_FAILED")
    return value.stdout


def properties(unit, keys):
    output = command(["systemctl", "show", unit, "--no-pager", *["-p" + key for key in keys]])
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def uint64_property(item):
    # get-property has scalar data; method-call reply arrays are not accepted here.
    need(type(item) is dict and item.get("type") == "t"
         and type(item.get("data")) is int
         and 0 <= item["data"] <= 2**64 - 1, "TIMER_DEADLINE_SCHEMA")
    return item["data"]


def error_diagnostic(stage, error):
    # Never record exception messages, filenames, source text or raw query values.
    allowed = ("RuntimeError", "TypeError", "ValueError", "KeyError", "IndexError",
               "AttributeError", "OSError", "PermissionError", "FileNotFoundError",
               "ProcessLookupError", "TimeoutExpired", "JSONDecodeError",
               "KeyboardInterrupt", "SystemExit", "MemoryError", "OverflowError")
    name = type(error).__name__
    trace = error.__traceback__
    line = 0
    while trace is not None:
        line = trace.tb_lineno
        trace = trace.tb_next
    return {"stage": stage, "error_class": name if name in allowed else "OtherError",
            "line": line}


def idle():
    jobs = json.loads(command(["busctl", "--json=short", "call", "org.freedesktop.systemd1",
                              "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "ListJobs"]))
    need(jobs.get("type") == "a(usssoo)" and isinstance(jobs.get("data"), list)
         and len(jobs["data"]) == 1 and isinstance(jobs["data"][0], list), "JOBS_SCHEMA")
    need(not jobs["data"][0], "HOST_JOB_BUSY_NO_INTERFERENCE_ASSUMPTION")
    for path in ("/var/lib/modelfc-deploy/service-pending.json",
                 "/var/lib/modelfc-deploy/test-request.json", "/run/modelfc-acquisition/request.json"):
        need(not os.path.lexists(path), "PENDING_REQUEST")
    names = command(["systemctl", "list-units", "--all", "--type=service", "--plain",
                     "--no-legend", "--no-pager"])
    for line in names.splitlines():
        name = line.split()[0]
        if not name.startswith("modelfc-") or not any(word in name for word in
                ("deploy", "postmerge", "acquisition", "depend", "suite", "validator", "handoff")):
            continue
        p = properties(name, ("MainPID", "ControlPID", "NFileDescriptorStore", "ControlGroup"))
        need(p.get("MainPID") == p.get("ControlPID") == "0"
             and p.get("NFileDescriptorStore", "0") == "0", "RELEVANT_WORKER_BUSY")
        if p.get("ControlGroup"):
            cgroup = pathlib.Path("/sys/fs/cgroup" + p["ControlGroup"])
            for member in cgroup.rglob("cgroup.procs"):
                need(not member.read_text().strip(), "WORKER_DESCENDANT_BUSY")
    # Detect fixed installed controller entry points, including SSH-launched workers.
    prefixes = (b"/opt/modelfc-deploy/", b"/opt/modelfc-validator/",
                b"/usr/lib/modelfc-suite/", b"/usr/lib/modelfc-suite-diag600/")
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except FileNotFoundError:
            continue
        need(not any(arg.startswith(prefixes) and arg.endswith(b".py") for arg in args),
             "CONTROLLER_ENTRY_POINT_BUSY")
    # Do not start just before scheduled collection/forecast/refresh work.
    timers = command(["systemctl", "list-units", "--type=timer", "--state=active", "--plain",
                      "--no-legend", "--no-pager"])
    for line in timers.splitlines():
        name = line.split()[0]
        if name.startswith("modelfc-"):
            obj = json.loads(command(["busctl", "--json=short", "call", "org.freedesktop.systemd1",
                                      "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                                      "GetUnit", "s", name]))
            need(obj.get("type") == "o" and len(obj.get("data", [])) == 1, "TIMER_OBJECT_SCHEMA")
            observed = {}
            for key, now in (("NextElapseUSecMonotonic", time.monotonic_ns() // 1000),
                             ("NextElapseUSecRealtime", time.time_ns() // 1000)):
                item = json.loads(command(["busctl", "--json=short", "get-property",
                                           "org.freedesktop.systemd1", obj["data"][0],
                                           "org.freedesktop.systemd1.Timer", key]))
                value = uint64_property(item)
                observed[key] = value
                need(value in (0, 2**64 - 1) or value - now > 90_000_000,
                     "PRODUCTION_TIMER_WITHIN_DIAGNOSTIC_WINDOW")
            events.append({"phase": "timer_observation", "unit": name, **observed})


def lock_metadata(path):
    value = os.lstat(path)
    need(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, "LOCK_FILE_TYPE")
    return [value.st_dev, value.st_ino, value.st_uid, value.st_gid,
            stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def production():
    paths = ["/usr/local/libexec/modelfc-prospective-launch.py",
             "/opt/modelfc-deploy/deploy_main.py", "/etc/apparmor.d/bwrap-userns-restrict",
             "/usr/bin/bwrap"]
    files = {path: digest(path) for path in paths}
    units = {}
    for entry in pathlib.Path("/etc/systemd/system").rglob("*"):
        if "modelfc" in str(entry) and entry.is_file() and not entry.is_symlink():
            files[str(entry)] = digest(entry)
    for name in ("modelfc-corner-api.service", "caddy.service"):
        units[name] = properties(name, ("Id", "LoadState", "ActiveState", "MainPID", "InvocationID",
                                        "ExecMainStartTimestampMonotonic", "RuntimeMaxUSec"))
    controls = {name: pathlib.Path("/proc/sys/kernel/" + name).read_text().strip() for name in
                ("unprivileged_userns_clone", "apparmor_restrict_unprivileged_userns")}
    return {"files": files, "units": units, "controls": controls,
            "loaded_profiles_sha256": digest("/sys/kernel/security/apparmor/profiles")}


def effective_unit():
    expected = {"DynamicUser": "yes", "User": USER, "RootDirectory": str(BASE / "rootfs"),
                "PrivateNetwork": "yes", "PrivateDevices": "yes", "ProtectHome": "yes",
                "ProtectSystem": "strict", "ProtectProc": "invisible", "ProcSubset": "all",
                "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
                "NoNewPrivileges": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "",
                "MemoryMax": "402653184", "MemorySwapMax": "0", "TasksMax": "32",
                "CPUQuotaPerSecUSec": "500ms", "RuntimeMaxUSec": "1min", "TimeoutStopUSec": "3s",
                "KillMode": "control-group", "DevicePolicy": "closed", "PrivateTmp": "yes",
                "RestrictSUIDSGID": "yes", "Restart": "no", "MountFlags": "private"}
    observed = properties(UNIT, tuple(expected) + ("RestrictAddressFamilies", "SystemCallFilter",
                           "SystemCallArchitectures", "RestrictNamespaces", "PrivateUsers", "DeviceAllow",
                           "ReadOnlyPaths", "ReadWritePaths", "BindReadOnlyPaths", "InaccessiblePaths", "TemporaryFileSystem"))
    result["effective_unit"] = observed
    need(all(observed.get(key) == value for key, value in expected.items()), "EFFECTIVE_UNIT_MISMATCH")
    need(set(observed.get("RestrictAddressFamilies", "").split()) == {"AF_UNIX", "AF_NETLINK"},
         "ADDRESS_FAMILY_MISMATCH")
    need(set(observed.get("ReadOnlyPaths", "").split()) ==
         {"+/", "+/dev", "+/tmp", "+/var/tmp", "+/empty-home"}, "ROOT_RELATIVE_READONLY_PATHS")
    need(set(observed.get("InaccessiblePaths", "").split()) ==
         {"+/dev/mqueue", "+/dev/hugepages"}, "ROOT_RELATIVE_INACCESSIBLE_PATHS")
    need(set(observed.get("TemporaryFileSystem", "").split()) ==
         {path + ":ro,nosuid,nodev,noexec,size=64K,nr_inodes=32,mode=0755"
          for path in ("/run", "/dev/shm")} | {'/run/zen53-bookkeeping:rw,nosuid,nodev,noexec,size=1M,nr_inodes=64,mode=1777,uid=0,gid=0'}, "PRIVATE_TMPFS_CONFIGURATION")
    need(observed.get("ReadWritePaths") == "",
         "BOOKKEEPING_WRITE_SCOPE")
    # No extra syscall/namespace/AppArmor allowances are issued by this observer.



def stop_provision_unit():
    props = properties(UNIT, ("ActiveState", "MainPID", "ControlPID", "ControlGroup", "InvocationID"))
    io = provision_transaction.io if provision_transaction is not None else None
    if props.get("ActiveState") == "inactive" and props.get("MainPID") == props.get("ControlPID") == "0":
        need(not props.get("ControlGroup"), "CLEANUP_INACTIVE_CGROUP")
        if io is not None and io.pidfd is not None:
            need(bool(select.select([io.pidfd], [], [], 0)[0]), "CLEANUP_PINNED_PROCESS_ALIVE")
        return
    identity = io.identity if io is not None else None
    need(identity is not None and props.get("InvocationID") == identity["invocation"]
         and props.get("ControlGroup") == identity["group"], "CLEANUP_FOREIGN_OR_UNKNOWN_INVOCATION")
    # Same cooperating-root-operators assumption as baseline. The query/stop pair
    # is not atomic against an unrelated root recreating this exact unit name.
    command(["systemctl", "stop", UNIT])


def provision_query():
    return properties(UNIT, ("MainPID", "InvocationID", "ControlGroup", "ActiveState", "SubState"))


def provision_guard():
    event_start = len(events)
    idle()
    current = production()
    need(current == before, "PRE_NATIVE_PRODUCTION_CHANGED")
    api = current["units"]["modelfc-corner-api.service"]
    age_us = time.monotonic_ns() // 1000 - int(api["ExecMainStartTimestampMonotonic"])
    need(0 <= age_us < 1_800_000_000 - 90_000_000, "PRE_NATIVE_API_ROTATION_TOO_NEAR")
    deadlines = [int(api["ExecMainStartTimestampMonotonic"]) * 1000 + 1_800_000_000_000 - 90_000_000_000]
    for event in events[event_start:]:
        if event.get("phase") != "timer_observation":
            continue
        for key in ("NextElapseUSecMonotonic", "NextElapseUSecRealtime"):
            value = event[key]
            if value in (0, 2**64 - 1):
                continue
            if key == "NextElapseUSecMonotonic":
                deadlines.append(value * 1000 - 90_000_000_000)
            else:
                # Sample monotonic first for a conservative realtime conversion.
                mono = time.monotonic_ns()
                deadlines.append(mono + value * 1000 - time.time_ns() - 90_000_000_000)
    return min(deadlines)


def sample(group, phase):
    cg = pathlib.Path("/sys/fs/cgroup" + group)
    pids = set()
    for file in cg.rglob("cgroup.procs") if cg.exists() else []:
        pids.update(int(value) for value in file.read_text().split())
    observations = []
    for pid in sorted(pids):
        proc = pathlib.Path("/proc") / str(pid)
        try:
            before = (proc / "stat").read_text()
            identity = before.rsplit(") ", 1)[1].split()[19]
            executable = os.readlink(proc / "exe")
            label = (proc / "attr/current").read_text().strip()
            membership = (proc / "cgroup").read_text().strip()
            need(membership.endswith(group) or group + "/" in membership, "CGROUP_ATTRIBUTION")
            record = {"pid": pid, "start_ticks": identity, "exe": executable, "label": label,
                      "namespace": {n: os.readlink(proc / "ns" / n) for n in ("pid", "mnt", "net", "user")},
                      "phase": phase, "utc": utc(), "monotonic_ns": time.monotonic_ns()}
            if executable == "/usr/bin/bwrap":
                # Read only the already pinned helper executable, never arbitrary contents.
                record["exe_sha256"] = digest(proc / "exe")
            if executable in ("/usr/bin/bwrap", "/usr/bin/python3.12", "/opt/codex/bin/codex"):
                data = (proc / "mountinfo").read_text()
                need(len(data) <= 131072, "OBSERVED_MOUNT_BOUND")
                record["mountinfo"] = data
            need((proc / "stat").read_text().rsplit(") ", 1)[1].split()[19] == identity,
                 "PROCESS_IDENTITY_CHANGED")
            observations.append(record)
        except (FileNotFoundError, ProcessLookupError):
            # Short-lived stages may be missed. Missing evidence cannot become PASS.
            continue
    return observations


def pinned_outer_failure_snapshot(io):
    """Bounded numeric context from the existing pidfd/procfd, never a new PID path."""
    record = {"state": "UNKNOWN", "monotonic_ns": time.monotonic_ns()}
    if io is None or io.pidfd is None or io.procfd is None or io.identity is None:
        return record
    record["expected_pid"] = io.identity["pid"]
    record["expected_start"] = io.identity["start"]
    try:
        ready = select.select([io.pidfd], [], [], 0)[0]
        record["state"] = "DEAD" if ready else "LIVE"
        if ready:
            return record
        current = parse_proc_identity(io.procfd, io.identity["invocation"], io.identity["group"])
        record["identity_match"] = current == io.identity
        if not record["identity_match"]:
            record["state"] = "MISMATCH"
            return record
        raw = read_at(io.procfd, "status", 16384).decode("ascii")
        rows = dict(line.split(":", 1) for line in raw.splitlines() if ":" in line)
        def numeric(value, limit):
            parts = value.split()
            if not 0 < len(parts) <= limit or not all(p.isascii() and p.isdecimal() for p in parts):
                raise ValueError("numeric snapshot")
            result = [int(p) for p in parts]
            if any(not 0 <= n < 2**32 for n in result):
                raise ValueError("numeric snapshot")
            return result
        record["nspid"] = numeric(rows["NSpid"], 8)
        record["uid"] = numeric(rows["Uid"], 4)
        record["gid"] = numeric(rows["Gid"], 4)
        if len(record["uid"]) != 4 or len(record["gid"]) != 4:
            raise ValueError("credential snapshot")
        for name in ("uid", "gid"):
            mapping = read_at(io.procfd, name + "_map", 256).decode("ascii").splitlines()
            if len(mapping) != 1:
                raise ValueError("mapping snapshot")
            values = numeric(mapping[0], 3)
            if len(values) != 3:
                raise ValueError("mapping snapshot")
            record[name + "_map"] = values
        for name in ("pid", "mnt", "net", "user"):
            link = os.readlink("ns/" + name, dir_fd=io.procfd)
            prefix = name + ":["
            if not link.startswith(prefix) or not link.endswith("]"):
                raise ValueError("namespace snapshot")
            value = link[len(prefix):-1]
            if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
                raise ValueError("namespace snapshot")
            record[name + "_ns"] = int(value)
        record["monotonic_end_ns"] = time.monotonic_ns()
        if record["monotonic_end_ns"] < record["monotonic_ns"]:
            raise ValueError("clock snapshot")
        if select.select([io.pidfd], [], [], 0)[0]:
            record["state"] = "DEAD_AT_END"
            return record
        if parse_proc_identity(io.procfd, io.identity["invocation"], io.identity["group"]) != io.identity:
            record["state"] = "MISMATCH"
            record["identity_match"] = False
        return record
    except (OSError, ValueError, KeyError, UnicodeError, TypeError, RuntimeError,
            IndexError, OverflowError):
        return {"state": "UNKNOWN", "monotonic_ns": record["monotonic_ns"],
                "expected_pid": io.identity["pid"], "expected_start": io.identity["start"]}


try:
    need(len(sys.argv) == 2 and sys.argv[1] == "--approved-launch", "SEPARATE_LAUNCH_APPROVAL_REQUIRED")
    need(os.geteuid() == 0, "ROOT_SUPERVISOR_REQUIRED")
    need(not (EVIDENCE / "result.json").exists(), "NO_REUSE_OR_SECOND_ATTEMPT")
    EVIDENCE.mkdir(mode=0o700, exist_ok=True)
    result["start_utc"] = utc()
    result["boot_id"] = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    manifest = json.loads((BASE / "ARTIFACT_SHA256.json").read_text())
    for name, expected in manifest.items():
        need(digest(BASE / name) == expected, "PACKAGE_DRIFT")
    dependencies = json.loads((BASE / "DEPENDENCIES.json").read_text())
    for alias, target in dependencies["symlinks"].items():
        need(os.readlink(BASE / "rootfs" / alias.lstrip("/")) == target, "RUNTIME_LINK_DRIFT")
    need(not (BASE / "rootfs/opt/codex/codex-resources/bwrap").exists(), "BUNDLED_FALLBACK_PRESENT")
    for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines():
        destination = line.split(" - ", 1)[0].split()[4].replace("\\040", " ")
        need(not destination.startswith(str(BASE) + "/"), "RESIDUAL_STAGING_MOUNT")
    actual = {"/" + str(path.relative_to(BASE / "rootfs"))
              for path in (BASE / "rootfs").rglob("*") if path.is_file() and not path.is_symlink()}
    need(actual == set(dependencies["dependencies"]) | {"/diagnostic/pins.json"}, "UNEXPECTED_ROOTFS_FILE")
    memory = dict(line.split(":", 1) for line in pathlib.Path("/proc/meminfo").read_text().splitlines())
    disk = os.statvfs(BASE)
    need(int(memory["MemAvailable"].split()[0]) >= 1536 * 1024 and os.getloadavg()[0] < 1.5
         and disk.f_bavail * disk.f_frsize >= 2 * 1024**3, "HEADROOM")
    idle()
    prior_locks = {}
    for path, inode, uid, gid, mode in LOCKS:
        for parent in pathlib.Path(path).parents:
            info = os.lstat(parent)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, 1000, 1001)
                 and not info.st_mode & 0o022, "LOCK_ANCESTOR_DRIFT")
        prior_locks[path] = lock_metadata(path)
        need(prior_locks[path][0] == 2049 and prior_locks[path][1:5] == [inode, uid, gid, mode], "LOCK_DRIFT")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fds.append(descriptor)
        need((os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) == (2049, inode), "LOCK_OPEN_DRIFT")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    idle()
    before = production()
    result["before"] = before
    need(before["units"]["modelfc-corner-api.service"]["ActiveState"] == "active", "API_NOT_ACTIVE")
    api = before["units"]["modelfc-corner-api.service"]
    need(api.get("RuntimeMaxUSec") == "30min" and int(api.get("MainPID", "0")) > 0
         and bool(api.get("InvocationID")), "API_RUNTIME_IDENTITY")
    age_us = time.monotonic_ns() // 1000 - int(api["ExecMainStartTimestampMonotonic"])
    need(0 <= age_us < 1_800_000_000 - 90_000_000, "ORDINARY_API_ROTATION_TOO_NEAR")
    need("bwrap (enforce)" in pathlib.Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines()
         and "unpriv_bwrap (enforce)" in pathlib.Path("/sys/kernel/security/apparmor/profiles").read_text().splitlines(),
         "PROFILE_DRIFT")
    need(before["files"]["/usr/bin/bwrap"] == "e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71"
         and before["files"]["/etc/apparmor.d/bwrap-userns-restrict"] ==
         "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9", "PREREQUISITE_DRIFT")
    service = []
    in_service = False
    for line in (BASE / "diagnostic.service").read_text().splitlines():
        if line.startswith("["):
            in_service = line == "[Service]"
        elif in_service and line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if key not in ("Type", "ExecStart"):
                service.append("--property=" + key + "=" + value)
    for name in ("stdout.log", "stderr.log"):
        fd = os.open(EVIDENCE / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    need(properties(UNIT, ("LoadState",))["LoadState"] == "not-found", "UNIT_COLLISION")
    try:
        pwd.getpwnam(USER)
        raise RuntimeError("IDENTITY_COLLISION")
    except KeyError:
        pass
    provision_transaction = ProvisionTransaction(ProvisionRootIO(BASE, provision_query, provision_guard))
    provision_transaction.begin()
    started = True
    command(["systemd-run", "--quiet", "--collect", "--unit=" + UNIT, "--service-type=exec", *service,
             "/usr/bin/python3", "-I", "-S", "-B", "/diagnostic/launch.py"])
    effective_unit()
    provision_transaction.prepare()
    end = time.monotonic() + 63
    seen, count = set(), 0
    while time.monotonic() < end:
        props = properties(UNIT, ("ActiveState", "SubState", "ControlGroup", "MainPID", "Result",
                                   "InvocationID", "MemoryPeak", "CPUUsageNSec", "TasksCurrent"))
        raw = (EVIDENCE / "stdout.log").read_bytes()
        need(len(raw) <= 65536, "CAPTURE_BOUND")
        request = unique_release_request(raw)
        if request is not None and provision_transaction.phase == "waiting_release":
            provision_transaction.release(request)
        if props.get("SubState") == "running":
            provision_transaction.io.check()
        native = b'"phase":"native_start"' in raw
        phase = "native" if native else "pre_native"
        if props.get("ControlGroup"):
            for record in sample(props["ControlGroup"], phase):
                signature = (record["pid"], record["start_ticks"], record["exe"], record["label"], phase)
                if signature not in seen:
                    seen.add(signature)
                    events.append(record)
                    count += 1
                    need(count <= 128, "OBSERVATION_BOUND")
        result["last_unit_properties"] = props
        if props.get("ActiveState") in ("inactive", "failed") or props.get("SubState") == "exited":
            break
        time.sleep(0.005)
    else:
        raise RuntimeError("SUPERVISOR_DEADLINE")
    raw = (EVIDENCE / "stdout.log").read_text()
    need('"phase":"launcher_complete"' in raw, "NATIVE_STARTUP_OR_REPORTER_FAILURE")
    result["native_runtime"] = "PASS"
    matching = [r for r in events if r.get("phase") == "native" and r.get("exe") == "/usr/bin/bwrap"
                and r.get("exe_sha256") == before["files"]["/usr/bin/bwrap"]
                and r.get("label") == "bwrap (enforce)"]
    report_lines = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("phase") == "native_finish":
            report_lines = item["output"].splitlines()
    labels = [line.split("=", 1)[1] for line in report_lines if line.startswith("apparmor=")]
    values = dict(line.split("=", 1) for line in report_lines if "=" in line)
    need(values.get("status.NoNewPrivs") == "1" and values.get("status.Seccomp") == "2"
         and all(int(values.get("status." + key, "-1"), 16) == 0
                 for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")), "REPORTED_PRIVILEGES")
    outer = [json.loads(line) for line in raw.splitlines() if line.startswith("{")]
    outer = [item for item in outer if item.get("phase") == "outer_admission"]
    need(bool(outer) and all(values.get("namespace." + name) != outer[-1]["namespaces"][name]
                             for name in ("pid", "mnt", "net", "user")), "FRESH_INNER_NAMESPACES_UNPROVEN")
    need(len(labels) == 1 and labels[0] == "bwrap//&unpriv_bwrap (enforce)"
         and matching, "SYSTEM_HELPER_TRANSITION_EVIDENCE_GAP")
    need(all(values.get("bookkeeping." + key) == "1" for key in
             ("readonly", "no_aliases", "no_inherited_fds", "outer_proc_denied")) and
         values.get("bookkeeping.tmpdir") == "/run/zen53-bookkeeping" and
         values.get("bookkeeping.proc_mode") in ("fresh", "inherited"),
         "BOOKKEEPING_REPORT_INCOMPLETE")
    need(values.get("bookkeeping.device") == outer[-1]["bookkeeping"]["device"] and
         values.get("bookkeeping.inode") == str(outer[-1]["bookkeeping"]["inode"]),
         "BOOKKEEPING_INNER_IDENTITY_MISMATCH")
    result["transition"] = "PASS"
    result["reporter"] = report_lines
    result["status"] = "COMPATIBILITY_PASS_NOT_CODING_ACCEPTANCE"
except BaseException as error:
    result["failure_diagnostic"] = error_diagnostic("supervisor", error)
    result["failure"] = result["failure_diagnostic"]["error_class"]
    try:
        result["pinned_outer_at_failure"] = pinned_outer_failure_snapshot(
            provision_transaction.io if provision_transaction is not None else None)
    except BaseException:
        # Observation failure cannot replace the original BLOCKED result.
        result["pinned_outer_at_failure"] = {"state": "UNKNOWN"}
finally:
    if started:
        try:
            stop_provision_unit()
            props = properties(UNIT, ("ActiveState", "MainPID", "ControlPID", "ControlGroup"))
            need(props.get("ActiveState") == "inactive" and props.get("MainPID") == "0"
                 and props.get("ControlPID") == "0", "TEMPORARY_UNIT_CLEANUP")
            need(not pathlib.Path("/sys/fs/cgroup/system.slice/" + UNIT).exists(), "CGROUP_REMAINS")
            try:
                pwd.getpwnam(USER)
                raise RuntimeError("DYNAMIC_IDENTITY_REMAINS")
            except KeyError:
                pass
            need(properties(UNIT, ("LoadState",))["LoadState"] == "not-found", "UNIT_REMAINS")
            for parent in (pathlib.Path("/tmp"), pathlib.Path("/var/tmp")):
                need(not list(parent.glob("systemd-private-*-" + UNIT + "-*")), "PRIVATE_TMP_REMAINS")
            for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines():
                destination = line.split(" - ", 1)[0].split()[4].replace("\\040", " ")
                need(not destination.startswith(str(BASE) + "/"), "TEMPORARY_MOUNT_REMAINS")
            result["cleanup"] = "PASS"
        except BaseException as error:
            result["cleanup_diagnostic"] = error_diagnostic("cleanup", error)
            result["cleanup"] = "BLOCKED:" + result["cleanup_diagnostic"]["error_class"]
            result["status"] = "BLOCKED"
    if provision_transaction is not None:
        result["provisioning_phase_at_cleanup"] = provision_transaction.phase
        result["release_may_have_committed"] = provision_transaction.phase in ("publishing_release", "released")
        try:
            provision_transaction.close()
        except BaseException as error:
            result["provisioning_close_error"] = error_diagnostic("provisioning_close", error)
            result["cleanup"] = "BLOCKED:PROVISION_DESCRIPTOR_CLEANUP"
            result["status"] = "BLOCKED"
    try:
        if "before" in locals():
            result["after"] = production()
            result["production_unchanged"] = result["after"] == before
            if not result["production_unchanged"]:
                result["status"] = "BLOCKED_PRODUCTION_CHANGE_REQUIRES_INVESTIGATION"
        if "prior_locks" in locals():
            result["lock_metadata_unchanged"] = all(lock_metadata(path) == value for path, value in prior_locks.items())
            need(result["lock_metadata_unchanged"], "FINAL_LOCK_DRIFT")
    except BaseException as error:
        result["final_inspection_diagnostic"] = error_diagnostic("final_inspection", error)
        result["final_inspection_error"] = result["final_inspection_diagnostic"]["error_class"]
        result["status"] = "BLOCKED"
    finally:
        retain = started and result.get("cleanup") != "PASS"
        if not retain:
            for descriptor in reversed(fds):
                os.close(descriptor)
    try:
        result["locks_released_by_close"] = 0 if retain else len(fds)
        result["cleanup_blocker_retains_supervisor_locks"] = retain
        if not retain and fds:
            rows = []
            for line in pathlib.Path("/proc/locks").read_text().splitlines():
                fields = line.split()
                if len(fields) >= 6 and fields[1] == "FLOCK" and fields[5] in ("08:01:543436", "08:01:535594"):
                    rows.append({"kind": fields[1], "mode": fields[3], "pid": fields[4], "identity": fields[5]})
            result["original_lock_owners_after_close"] = rows
        result["finish_utc"] = utc()
        # Outside the payload root, bounded private output; no report overwrite/retry.
        if EVIDENCE.is_dir() and not (EVIDENCE / "result.json").exists():
            payload = json.dumps({"result": result, "events": events}, indent=2).encode()
            if len(payload) > 2 * 1024**2:
                result["status"] = "BLOCKED_EVIDENCE_BOUND"
                payload = json.dumps({"result": result, "events_truncated": True}).encode()
            fd = os.open(EVIDENCE / "result.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    finally:
        if retain:
            # Do not reopen admission while temporary descendants are unverified.
            # This is a dormant operator hold, not a retry or extended payload run.
            # A separate recovery decision is required; inspect the retained result.
            while True:
                time.sleep(60)
