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
