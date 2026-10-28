def read_proc_identity_at(procfd, name, limit=4096):
    # Called only by the authenticated pinned-proc identity parser. No pathname
    # reopening, no malformed-data fallback, and no arbitrary I/O error masking.
    require(name in ("stat", "status", "cgroup"), "PROVISION_PROC_READ_TARGET")
    try:
        return read_at(procfd, name, limit)
    except OSError as error:
        if error.errno not in (2, 3):  # ENOENT / ESRCH, not EIO/EACCES/EBADF.
            raise
        require(False, "PROVISION_PROC_DISAPPEARED")


def completed_provision_unit(transaction):
    # A terminal observation permits final proof verification, never acceptance.
    need(transaction is not None and transaction.phase == "released",
         "COMPLETION_NOT_RELEASED")
    io, identity = transaction.io, transaction.identity
    need(identity is not None and io.identity == identity and io.pidfd is not None,
         "COMPLETION_PIN_MISSING")
    valid_identity(identity)
    need(identity["group"] == "/system.slice/" + UNIT, "COMPLETION_GROUP")
    expected = {
        "LoadState": "loaded", "ActiveState": "active", "SubState": "exited",
        "MainPID": "0", "ControlPID": "0", "ControlGroup": "",
        "InvocationID": identity["invocation"], "RootDirectory": str(BASE / "rootfs"),
        "ExecMainPID": str(identity["pid"]), "ExecMainCode": "1",
        "ExecMainStatus": "0", "Result": "success",
    }
    # ExecMainCode 1 is CLD_EXITED; require the recorded normal, successful exit.
    io.stage_check()
    for _ in range(2):
        need(bool(select.select([io.pidfd], [], [], 0)[0]), "COMPLETION_PROCESS_LIVE")
        need(properties(UNIT, tuple(expected)) == expected, "COMPLETION_UNIT_BINDING")
        try:
            os.stat("/sys/fs/cgroup" + identity["group"], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            need(False, "COMPLETION_CGROUP_REMAINS")
        io.stage_check()
    return expected


def monitor_provision(transaction):
    try:
        transaction.io.check()
    except RuntimeError as error:
        diagnostic = error_diagnostic("supervisor", error)
        # Only authenticated exit observations can attempt terminal reconciliation.
        # Malformed data, identity/credential changes and stage errors still fail.
        if diagnostic["predicate"] not in ("PROVISION_PIDFD_DEAD", "PROVISION_UNIT_IDENTITY_CHANGED",
                                            "PROVISION_PROCESS_DEAD", "PROVISION_PROC_DISAPPEARED"):
            raise
        return completed_provision_unit(transaction)
    return None


def error_diagnostic(stage, error):
    # Fixed code identities/line numbers only: never messages, locals or paths.
    allowed = ("RuntimeError", "TypeError", "ValueError", "KeyError", "IndexError",
               "AttributeError", "OSError", "PermissionError", "FileNotFoundError",
               "ProcessLookupError", "TimeoutExpired", "JSONDecodeError",
               "KeyboardInterrupt", "SystemExit", "MemoryError", "OverflowError")
    name = type(error).__name__
    trace, frames, count, prior, terminal = error.__traceback__, [], 0, None, None
    while trace is not None and count < 64:
        frame = {"function": DIAGNOSTIC_CODES.get(trace.tb_frame.f_code, "UNKNOWN"),
                 "line": trace.tb_lineno if trace.tb_frame.f_code in DIAGNOSTIC_CODES else 0}
        frames.append(frame)
        if len(frames) > 8:
            frames.pop(0)
        prior, terminal = terminal, (trace.tb_frame.f_code, frame)
        trace = trace.tb_next
        count += 1
    predicate = "UNKNOWN"
    if trace is None and terminal is not None and prior is not None and type(error) is RuntimeError and len(error.args) == 1 and type(error.args[0]) is str:
        helper, _ = terminal
        caller = prior[1]
        expected = DIAGNOSTIC_ASSERTIONS.get((caller["function"], caller["line"]))
        if helper in (require.__code__, need.__code__) and expected is not None and error.args == (expected,):
            predicate = expected
        elif helper is proof_need.__code__ and error.args == ("PROVISIONAL_PROOF_INVALID",):
            predicate = "PROVISIONAL_PROOF_INVALID"
    return {"stage": stage if stage in ("supervisor", "cleanup", "provisioning_close", "final_inspection") else "UNKNOWN",
            "error_class": name if name in allowed else "OtherError", "predicate": predicate,
            "trace": frames, "trace_truncated": count > 8 or trace is not None,
            "line": terminal[1]["line"] if terminal is not None else 0}
