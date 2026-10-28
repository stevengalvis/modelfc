"""Offline exact-site instrumentation of generated launcher bytes only."""
import ast
from pathlib import Path

RUNTIME = Path(__file__).with_name('proc_bracket_runtime.inc').read_text('ascii')


def instrument(raw):
    source = raw.decode('ascii')
    if 'native_bracket' in source or 'class ProcBracket:' in source:
        raise ValueError('BRACKET_INSTRUMENTATION_SITE')
    edits = (
        ('class NativeOutput:', RUNTIME + '\n\nclass NativeOutput:'),
        ('            lower = line.lower()',
         '            native_bracket.observe(line)\n            lower = line.lower()'),
        ('        provision_worker.before_native()\n    pid = os.fork()',
         '        native_bracket.begin()\n        provision_worker.before_native()\n    pid = os.fork()'),
        ('    if provision_worker is not None:\n        provision_worker.close()',
         '    native_bracket.close()\n    if provision_worker is not None:\n        provision_worker.close()'),
    )
    for old, new in edits:
        if source.count(old) != 1:
            raise ValueError('BRACKET_INSTRUMENTATION_SITE')
        source = source.replace(old, new, 1)
    ast.parse(source)
    return source.encode('ascii')
