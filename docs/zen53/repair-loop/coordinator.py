"""Two-attempt trusted-caller journal, not a model runner or authentication service.

Production callers supply a root-owned private directory FD and independently
verified runtime/capture receipts. The fixture API is restricted to the current
non-root UID. This journal bounds delivery in this retained directory only: it
cannot stop a privileged caller fabricating receipts or creating a different loop.
Pending/partial records fail closed. No replay executes or retries any operation.
"""
import importlib.util
import os
from pathlib import Path
import stat


def _module(name):
    spec = importlib.util.spec_from_file_location('repair_loop_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c = _module('contract')


def _usage():
    return _module('usage')


def _copy(value):
    return c.loads(c.canonical(value), 16384)


def validate_receipt(value, reservation, proposal, capture_sha256):
    """Validate a trusted caller's aggregate; model output is never this receipt."""
    keys = {'version', 'loop_id', 'attempt', 'request_id', 'feedback_sha256',
            'response_sha256', 'capture_sha256', 'candidate_sha256', 'stage_id',
            'stage_manifest_sha256', 'native', 'transition', 'cleanup', 'proof', 'readback', 'task', 'cases', 'usage'}
    c.need(type(value) is dict and set(value) == keys)
    c.need(type(value['version']) is int and value['version'] == 1)
    c.bindings({key: value[key] for key in ('loop_id', 'attempt', 'request_id', 'feedback_sha256')})
    for key in ('loop_id', 'attempt', 'request_id', 'feedback_sha256', 'stage_id'):
        c.need(value[key] == reservation[key])
    c.need(c.hex64(capture_sha256) and value['capture_sha256'] == capture_sha256)
    c.need(value['response_sha256'] == proposal['response_sha256']
           and value['candidate_sha256'] == proposal['replacement_sha256']
           and c.hex64(value['stage_manifest_sha256']))
    c.need(all(value[key] == 'PASS' for key in ('native', 'transition', 'cleanup', 'proof', 'readback')))
    c.need(type(value['cases']) is list and len(value['cases']) == 2)
    op = next(op for op, data in c.CANDIDATES.items() if data.decode('ascii') == proposal['replacement'])
    actuals = {'+': (5, 0), '-': (-1, -4), '*': (6, -4)}[op]
    for case, case_id, expected, actual in zip(value['cases'], ('add_positive', 'add_cancel'), (5, 0), actuals):
        c.need(type(case) is dict and set(case) == {'case_id', 'expected', 'actual'})
        c.need(case['case_id'] == case_id and type(case['expected']) is int and case['expected'] == expected)
        c.need(type(case['actual']) is int and -100 <= case['actual'] <= 100 and case['actual'] == actual)
    task = 'PASS' if all(case['actual'] == case['expected'] for case in value['cases']) else 'FAIL'
    c.need(value['task'] == task)
    _usage().validate_report(value['usage'])
    return _copy(value)


class Loop:
    def __init__(self, directory_fd, loop_id, owner):
        c.need(c.hex64(loop_id))
        self.fd = os.dup(directory_fd)
        try:
            info = os.fstat(self.fd)
            c.need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
                   and (info.st_uid, info.st_gid) == owner)
            self.identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
            self.owner, self.loop_id = owner, loop_id
            if not os.listdir(self.fd):
                self._write('loop.json', {'version': 1, 'loop_id': loop_id})
            self._state()
        except BaseException:
            fd, self.fd = self.fd, None
            os.close(fd)
            raise

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def _check(self):
        c.need(self.fd is not None)
        info = os.fstat(self.fd)
        c.need((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) == self.identity)

    def _read(self, name):
        self._check()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            before = os.fstat(fd)
            c.need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600
                   and (before.st_uid, before.st_gid) == self.owner and before.st_nlink == 1
                   and 0 < before.st_size <= 16384)
            raw = os.read(fd, 16385)
            c.need(len(raw) == before.st_size and not os.read(fd, 1))
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            expected = tuple(getattr(before, key) for key in fields)
            c.need(expected == tuple(getattr(os.fstat(fd), key) for key in fields)
                   == tuple(getattr(os.stat(name, dir_fd=self.fd, follow_symlinks=False), key) for key in fields))
            value = c.loads(raw, 16384)
            c.need(c.canonical(value) == raw)
            # Reopened completed records are durable before permitting a next attempt.
            os.fsync(fd)
            return value
        finally:
            os.close(fd)

    def _write(self, name, value):
        self._check()
        raw = c.canonical(value)
        c.need(len(raw) <= 16384)
        pending = name + '.pending'
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.fd)
        try:
            os.fchmod(fd, 0o600)
            offset = 0
            while offset < len(raw):
                count = os.write(fd, raw[offset:])
                c.need(count > 0)
                offset += count
            os.fsync(fd)
        finally:
            os.close(fd)
        # Exclusive link publication; no overwrite, replacement or partial recovery.
        os.link(pending, name, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        os.unlink(pending, dir_fd=self.fd)
        os.fsync(self.fd)

    def _feedback(self, previous):
        if previous is None:
            return {'version': 1, 'loop_id': self.loop_id, 'state': 'NO_PREVIOUS_ATTEMPT'}
        receipt = previous['receipt']
        return {'version': 1, 'loop_id': self.loop_id, 'attempt': receipt['attempt'],
                'request_id': receipt['request_id'], 'capture_sha256': receipt['capture_sha256'],
                'candidate_sha256': receipt['candidate_sha256'], 'result_sha256': c.digest(previous),
                'cases': _copy(receipt['cases'])}

    def _state(self):
        self._check()
        names = set(os.listdir(self.fd))
        allowed = {'loop.json'} | {f'attempt{n}.{kind}.json' for n in (1, 2) for kind in ('reserve', 'result')}
        c.need(names <= allowed and 'loop.json' in names)
        c.need(c.canonical(self._read('loop.json')) == c.canonical({'version': 1, 'loop_id': self.loop_id}))
        records, reservations = [], []
        for attempt in (1, 2):
            reserve_name, result_name = f'attempt{attempt}.reserve.json', f'attempt{attempt}.result.json'
            if reserve_name not in names:
                c.need(result_name not in names)
                continue
            c.need(attempt == len(reservations) + 1 and (attempt == 1 or len(records) == 1))
            if records:
                c.need(records[-1]['receipt']['task'] == 'FAIL')
            reservation = self._read(reserve_name)
            c.need(type(reservation) is dict and set(reservation) == {
                'version', 'loop_id', 'attempt', 'request_id', 'feedback_sha256', 'stage_id'})
            c.bindings({key: reservation[key] for key in ('loop_id', 'attempt', 'request_id', 'feedback_sha256')})
            c.need(type(reservation['version']) is int and reservation['version'] == 1
                   and reservation['loop_id'] == self.loop_id and reservation['attempt'] == attempt
                   and c.hex64(reservation['stage_id']))
            c.need(reservation['feedback_sha256'] == c.digest(self._feedback(records[-1] if records else None)))
            c.need(all(reservation['request_id'] != old['request_id'] and reservation['stage_id'] != old['stage_id']
                       for old in reservations))
            reservations.append(reservation)
            if result_name not in names:
                continue
            record = self._read(result_name)
            c.need(type(record) is dict and set(record) == {'version', 'reservation_sha256', 'proposal_raw', 'receipt', 'usage_aggregate'})
            c.need(type(record['version']) is int and record['version'] == 1
                   and record['reservation_sha256'] == c.digest(reservation) and type(record['proposal_raw']) is str)
            proposal = c.validate_proposal(record['proposal_raw'].encode('utf-8'),
                {key: reservation[key] for key in ('loop_id', 'attempt', 'request_id', 'feedback_sha256')})
            c.need(type(record['receipt']) is dict)
            receipt = validate_receipt(record['receipt'], reservation, proposal, record['receipt'].get('capture_sha256'))
            c.need(all(receipt['stage_manifest_sha256'] != old['receipt']['stage_manifest_sha256']
                       and receipt['capture_sha256'] != old['receipt']['capture_sha256'] for old in records))
            usage = _usage().aggregate([old['receipt']['usage'] for old in records] + [receipt['usage']])
            c.need(c.canonical(record['usage_aggregate']) == c.canonical(usage))
            records.append(record)
        os.fsync(self.fd)
        return reservations, records

    def reserve(self, request_id, stage_id):
        reservations, records = self._state()
        c.need(len(reservations) == len(records) and len(reservations) < 2)
        c.need(not records or records[-1]['receipt']['task'] == 'FAIL')
        c.need(c.hex64(request_id) and c.hex64(stage_id))
        c.need(all(request_id != old['request_id'] and stage_id != old['stage_id'] for old in reservations))
        feedback = self._feedback(records[-1] if records else None)
        reservation = dict(version=1, loop_id=self.loop_id, attempt=len(reservations) + 1,
                           request_id=request_id, feedback_sha256=c.digest(feedback), stage_id=stage_id)
        self._write(f"attempt{reservation['attempt']}.reserve.json", reservation)
        return {'reservation': reservation, 'feedback': feedback}

    def record_result(self, proposal_raw, capture_sha256, receipt):
        reservations, records = self._state()
        c.need(len(reservations) == len(records) + 1)
        reservation = reservations[-1]
        expected = {key: reservation[key] for key in ('loop_id', 'attempt', 'request_id', 'feedback_sha256')}
        proposal = c.validate_proposal(proposal_raw, expected)
        checked = validate_receipt(receipt, reservation, proposal, capture_sha256)
        c.need(all(checked['stage_manifest_sha256'] != old['receipt']['stage_manifest_sha256']
                   and capture_sha256 != old['receipt']['capture_sha256'] for old in records))
        record = dict(version=1, reservation_sha256=c.digest(reservation), proposal_raw=proposal_raw.decode('utf-8'),
                      receipt=checked, usage_aggregate=_usage().aggregate(
                          [old['receipt']['usage'] for old in records] + [checked['usage']]))
        self._write(f"attempt{reservation['attempt']}.result.json", record)
        return _copy(record)


def open_loop(directory_fd, loop_id):
    c.need(os.geteuid() == 0)
    return Loop(directory_fd, loop_id, (0, 0))


def open_loop_fixture(directory_fd, loop_id):
    c.need(os.geteuid() != 0)
    return Loop(directory_fd, loop_id, (os.geteuid(), os.getegid()))
