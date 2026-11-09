"""Trusted comparison and narrowly restricted container-only candidate loader.

Only a future reviewed restricted executor may supply candidate. Never import a
candidate module here or treat candidate stdout as a result. Cases and this
comparison must remain outside candidate-controlled namespaces and mounts.
"""
import ast

from .trusted_tests import CASES


def compare(candidate):
    results = []
    for name, original, field, expected, error in CASES:
        row = dict(original)
        try:
            actual = candidate(row, field)
        except Exception as exc:
            passed = error is not None and type(exc) is error[0] and exc.args == (error[1],)
        else:
            passed = error is None and type(actual) is str and actual == expected
        results.append((name, passed and row == original))
    return tuple(results)


SIGNATURE = 'def required_text(row: dict[str, str | None], field: str) -> str:\n    pass\n'

def validate_function(source):
    if type(source) is not str or len(source.encode()) > 8192:
        raise ValueError('function_bound')
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError('function_shape')
    function = tree.body[0]
    original = ast.parse(SIGNATURE).body[0]
    if (function.name != original.name or function.decorator_list
            or getattr(function, 'type_params', [])
            or ast.dump(function.args) != ast.dump(original.args)
            or ast.dump(function.returns) != ast.dump(original.returns)):
        raise ValueError('function_signature')
    allowed = (ast.Assign, ast.If, ast.IfExp, ast.Return, ast.Raise, ast.Expr, ast.Constant,
               ast.Name, ast.Load, ast.Store, ast.Subscript, ast.Call, ast.Attribute,
               ast.Compare, ast.Is, ast.IsNot, ast.BoolOp, ast.Or, ast.And,
               ast.UnaryOp, ast.Not, ast.JoinedStr, ast.FormattedValue)
    nodes = [node for statement in function.body for node in ast.walk(statement)]
    if len(nodes) > 128:
        raise ValueError('candidate_complexity')
    for node in nodes:
        if not isinstance(node, allowed):
            raise ValueError('candidate_grammar')
        if isinstance(node, ast.Name):
            if (node.id not in {'row', 'field', 'value', 'stripped', 'ValueError'}
                    or isinstance(node.ctx, ast.Store) and node.id not in {'value', 'stripped'}):
                raise ValueError('candidate_name')
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise ValueError('candidate_assignment')
        if isinstance(node, ast.Subscript):
            if ast.dump(node) != ast.dump(ast.parse('row[field]', mode='eval').body):
                raise ValueError('candidate_subscript')
        if isinstance(node, ast.Attribute):
            if (node.attr != 'strip' or not isinstance(node.value, ast.Name)
                    or node.value.id not in {'value', 'stripped'}):
                raise ValueError('candidate_attribute')
        if isinstance(node, ast.Call):
            strip = isinstance(node.func, ast.Attribute) and not node.args
            error = isinstance(node.func, ast.Name) and node.func.id == 'ValueError' and len(node.args) == 1
            if not (strip or error) or node.keywords:
                raise ValueError('candidate_call')
        if isinstance(node, ast.Constant) and not (
                node.value is None or type(node.value) is str and len(node.value) <= 128):
            raise ValueError('candidate_constant')
        if isinstance(node, ast.FormattedValue) and (
                not isinstance(node.value, ast.Name) or node.value.id != 'field'
                or node.conversion != -1 or node.format_spec is not None):
            raise ValueError('candidate_format')
    if sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and node.func.attr == 'strip' for node in nodes) != 1:
        raise ValueError('single_strip_required')
    return tree


def run_candidate(source):
    """Container only. No module import and no candidate access to the harness."""
    tree = validate_function(source)
    function = tree.body[0]
    function.returns = None
    for argument in function.args.args:
        argument.annotation = None
    namespace = {'__builtins__': {'ValueError': ValueError}}
    exec(compile(tree, '<required_text>', 'exec'), namespace)
    return compare(namespace['required_text'])
