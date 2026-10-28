"""Exact-source local generation of bounded diagnostics and terminal cleanup."""
import ast
from pathlib import Path

RUNTIME = Path(__file__).with_name('harness_runtime.inc').read_text('ascii')


def instrument(raw):
    source = raw.decode('ascii')
    tree = ast.parse(source)
    old = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'error_diagnostic')
    lines = source.splitlines(keepends=True)
    lines[old.lineno-1:old.end_lineno] = [RUNTIME + '\n']
    source = ''.join(lines)
    marker = '    identity = io.identity if io is not None else None\n'
    if source.count(marker) != 1:
        raise ValueError('HARNESS_CLEANUP_SITE')
    branch = """    identity = io.identity if io is not None else None
    if props.get("ActiveState") == "active" and props.get("SubState") == "exited":
        def terminal_binding():
            current = properties(UNIT, ("ActiveState", "SubState", "MainPID", "ControlPID",
                                       "ControlGroup", "InvocationID", "RootDirectory"))
            need(identity is not None and current == {
                "ActiveState": "active", "SubState": "exited", "MainPID": "0", "ControlPID": "0",
                "ControlGroup": "", "InvocationID": identity["invocation"],
                "RootDirectory": str(BASE / "rootfs")}, "CLEANUP_TERMINAL_BINDING")
            need(identity["group"] == "/system.slice/" + UNIT and io.pidfd is not None
                 and bool(select.select([io.pidfd], [], [], 0)[0]), "CLEANUP_TERMINAL_PROCESS")
            need(not pathlib.Path("/sys/fs/cgroup" + identity["group"]).exists(),
                 "CLEANUP_TERMINAL_CGROUP")
        terminal_binding()
        terminal_binding()  # Immediate identity/resource recheck before the one ordinary stop.
        command(["systemctl", "stop", UNIT])
        return
"""
    source = source.replace(marker, branch, 1)
    query = 'props = properties(UNIT, ("ActiveState", "MainPID", "ControlPID", "ControlGroup", "InvocationID"))'
    if source.count(query) != 1:
        raise ValueError('HARNESS_CLEANUP_QUERY')
    source = source.replace(query, query.replace('"ActiveState",', '"ActiveState", "SubState",'), 1)
    monitoring = '        if props.get("SubState") == "running":\n            provision_transaction.io.check()'
    terminal = '        if props.get("ActiveState") in ("inactive", "failed") or props.get("SubState") == "exited":\n            break'
    if source.count(monitoring) != 1 or source.count(terminal) != 1:
        raise ValueError('HARNESS_COMPLETION_SITE')
    source = source.replace(monitoring,
        '        if props.get("SubState") == "running":\n'
        '            completion = monitor_provision(provision_transaction)\n'
        '            if completion is not None:\n'
        '                result["terminal_completion"] = completion\n'
        '                break', 1)
    source = source.replace(terminal,
        '        if props.get("ActiveState") in ("inactive", "failed") or props.get("SubState") == "exited":\n'
        '            result["terminal_completion"] = completed_provision_unit(provision_transaction)\n'
        '            break', 1)
    marker = '\ntry:\n    need(len(sys.argv)'
    if source.count(marker) != 1:
        raise ValueError('HARNESS_REGISTRY_SITE')
    registry = '\nDIAGNOSTIC_CODES = {}\nDIAGNOSTIC_ASSERTIONS = {}\n'
    source = source.replace(marker, registry + marker, 1)
    tree = ast.parse(source)
    # Only code objects reachable through fixed authenticated top-level definitions
    # are named. Nested/unrecognized code never contributes a name or predicate.
    refs = {'MODULE': 'sys._getframe().f_code'}
    scopes = []
    compiled = compile(source, '<authenticated-supervisor>', 'exec')
    def register(node, ref, code):
        key = 'F%03d' % len(refs)
        refs[key] = ref
        scopes.append((node, key))
        def declarations(parent):
            for child in ast.iter_child_nodes(parent):
                if isinstance(child, ast.FunctionDef):
                    yield child
                else:
                    yield from declarations(child)
        for nested in declarations(node):
            index = next(i for i,c in enumerate(code.co_consts)
                             if hasattr(c, 'co_name') and c.co_name == nested.name
                             and c.co_firstlineno == nested.lineno)
            nested_ref = 'next(c for c in ' + ref + '.co_consts if getattr(c, \'co_name\', None) == ' + repr(nested.name) + ' and c.co_firstlineno == ' + str(nested.lineno) + ')'
            register(nested, nested_ref, code.co_consts[index])
    def code_for(container, node):
        return next(c for c in container.co_consts if hasattr(c, 'co_name')
                    and c.co_name == node.name and c.co_firstlineno == node.lineno)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            register(node, node.name + '.__code__', code_for(compiled, node))
        elif isinstance(node, ast.ClassDef):
            parent = code_for(compiled, node)
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    register(child, node.name + '.' + child.name + '.__code__', code_for(parent, child))
    assertions = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in ('need', 'require'):
            continue
        if len(node.args) != 2 or not isinstance(node.args[1], ast.Constant) or not isinstance(node.args[1].value, str):
            continue
        owners = [(n,k) for n,k in scopes if n.lineno <= node.lineno <= n.end_lineno]
        owner = min(owners, key=lambda item: item[0].end_lineno-item[0].lineno)[1] if owners else 'MODULE'
        assertions[(owner,node.lineno)] = node.args[1].value
    source = source.replace('DIAGNOSTIC_CODES = {}', 'DIAGNOSTIC_CODES = {' + ', '.join(ref+': '+repr(key) for key,ref in refs.items()) + '}', 1)
    source = source.replace('DIAGNOSTIC_ASSERTIONS = {}', 'DIAGNOSTIC_ASSERTIONS = '+repr(assertions), 1)
    ast.parse(source)
    return source.encode('ascii')
