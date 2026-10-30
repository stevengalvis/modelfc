"""Execute only AST-selected launcher output handling with fake pipe/process IO."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

LAUNCH = Path(__file__).resolve().parents[1] / 'candidate/launch.py'


def _launcher_code():
    tree = ast.parse(LAUNCH.read_text())
    selected = [node for node in tree.body if
                isinstance(node, (ast.FunctionDef, ast.ClassDef)) and
                node.name in ('need', 'NativeOutput', 'fixed_command') or
                isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'EXPECTED_ALIAS_WARNING'
                    for target in node.targets)]
    return compile(ast.Module(body=selected, type_ignores=[]), '<launcher-parent-only>', 'exec')


LAUNCHER_CODE = _launcher_code()


def capture(chunks):
    events = []
    queue = iter([chunk for chunk in chunks if chunk])
    fake = NS(O_CLOEXEC=1, WNOHANG=1, pipe2=lambda flags: (10, 11),
              fork=lambda: 99, close=lambda fd: None, set_blocking=lambda *args: None,
              read=lambda *args: next(queue), waitpid=lambda *args: (0, 0))
    env = {'os': fake, 'time': NS(monotonic=lambda: 0),
           'select': NS(select=lambda *args: ([10], [], [])),
           'emit': lambda phase, **kwargs: events.append((phase, kwargs)),
           'bookkeeping_recheck': lambda: None,
           'provision_worker': NS(before_native=lambda: None)}
    exec(LAUNCHER_CODE, env)
    try:
        env['fixed_command'](['fake-native'], 35, 'native')
    except BaseException as error:
        return error, [kwargs['output'].encode() for phase, kwargs in events
                       if phase == 'native_stop_evidence']
    raise AssertionError('fatal diagnostic unexpectedly accepted')
