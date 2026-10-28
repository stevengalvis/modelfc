"""Exact-site offline generation of the reviewed provisional proof protocol."""
import ast
from pathlib import Path

RUNTIME = Path(__file__).with_name('provisional_runtime.inc').read_text('ascii')


def change(source, old, new):
    if source.count(old) != 1:
        raise ValueError('PROVISIONAL_GENERATION_SITE')
    return source.replace(old, new, 1)


def launcher(raw):
    source = raw.decode('ascii')
    source = change(source, 'class NativeOutput:', RUNTIME + '\n\nproof_outer = ProofOuter()\n\nclass NativeOutput:')
    source = change(source, '        self.alias_warnings = 0', '        self.alias_warnings = 0\n        self.proof_frames = 0')
    source = change(source, '            if line == EXPECTED_ALIAS_WARNING:',
        "            if line.startswith(PROOF_PREFIX.encode()):\n                proof_parse(line)\n                self.proof_frames += 1\n                proof_need(self.proof_frames == 1)\n                continue\n            if line == EXPECTED_ALIAS_WARNING:")
    source = change(source, '        need(not self.pending, "NATIVE_OUTPUT_UNTERMINATED")',
        '        need(not self.pending, "NATIVE_OUTPUT_UNTERMINATED")\n        proof_need(self.proof_frames == 1)')
    source = change(source, '        provision_worker.before_native()\n    pid = os.fork()',
        '        proof_outer.begin()\n        provision_worker.before_native()\n    pid = os.fork()')
    source = change(source, '    need(output.count(b"ZEN53_REPORT_V1\\n") == 1 and output.count(b"ZEN53_REPORT_END\\n") == 1,\n         "REPORT_MISSING_OR_DUPLICATE")',
        '    proof_outer.finish(output)')
    source = change(source, '    native_bracket.close()\n    if provision_worker is not None:\n        provision_worker.close()',
        '    try:\n        proof_outer.close()\n    finally:\n        try:\n            native_bracket.close()\n        finally:\n            if provision_worker is not None:\n                provision_worker.close()')
    ast.parse(source)
    return source.encode('ascii')


def reporter(raw):
    source = raw.decode('ascii')
    source = change(source, 'def main():', RUNTIME + '\n\nproof_inner_record = None\n\ndef main():')
    source = change(source, '    global INNER_ROOT_UID, INNER_ROOT_GID',
        '    global INNER_ROOT_UID, INNER_ROOT_GID, proof_inner_record')
    source = change(source, '    deny_outer_aliases(outer_pid, fresh, context)',
        '    proof_need(not fresh)\n    provisional = proof_collect(outer_pid, raw, attempt, provision, release, map_rows, namespaces)')
    source = change(source, "    return ['bookkeeping.readonly=1', 'bookkeeping.no_aliases=1',",
        "    provisional['fd_checked'] = True\n    provisional['topology_checked'] = True\n    proof_inner_record = provisional\n    return ['bookkeeping.readonly=1', 'bookkeeping.no_aliases=1',")
    source = change(source, "'bookkeeping.outer_proc_denied=1'", "'bookkeeping.outer_proc_denied=provisional'")
    source = change(source, '    lines += ["status." + key + "=" + status[key] for key in FIELDS]',
        '    lines += ["status." + key + "=" + (" ".join(status[key].split()) if key in ("Uid", "Gid", "NSpid") else status[key]) for key in FIELDS]')
    source = change(source, '    output = "\\n".join(lines) + "\\nZEN53_REPORT_END\\n"',
        "    lines.append('ZEN53_REPORT_END')\n    frame = {'version': 1, 'observation': proof_inner_record, 'report': lines}\n    output = PROOF_PREFIX + proof_bytes(frame).decode('ascii') + '\\n'\n    proof_parse(output.encode('ascii'))")
    ast.parse(source)
    return source.encode('ascii')


def supervisor(raw):
    source = raw.decode('ascii')
    source = change(source, 'def need(ok, code):', RUNTIME + '\n\ndef need(ok, code):')
    source = change(source, '    need(\'"phase":"launcher_complete"\' in raw, "NATIVE_STARTUP_OR_REPORTER_FAILURE")',
                    "    verified_report_lines = proof_supervisor(raw, {'identity': provision_transaction.identity,\n        'nonce': provision_transaction.attempt['nonce'],\n        'provision': hashlib.sha256(canonical_record(provision_transaction.provision)).hexdigest()})")
    begin = '    report_lines = []\n'
    end = '    labels = [line.split("=", 1)[1] for line in report_lines if line.startswith("apparmor=")]'
    start = source.index(begin)
    stop = source.index(end, start)
    source = source[:start] + '    report_lines = verified_report_lines\n' + source[stop:]
    source = change(source, '    values = dict(line.split("=", 1) for line in report_lines if "=" in line)',
                    '    values = proof_report(report_lines)')
    source = change(source, '("readonly", "no_aliases", "no_inherited_fds", "outer_proc_denied")) and',
                    '("readonly", "no_aliases", "no_inherited_fds")) and\n         values.get("bookkeeping.outer_proc_denied") == "provisional" and')
    ast.parse(source)
    return source.encode('ascii')
