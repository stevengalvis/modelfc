"""Restricted syntax and fixed synthetic assertions for this one small utility.

This narrows the accepted function; it does not replace the OS sandbox. Never
call run_tests on model-produced source outside the approved disposable container.
"""
import ast
import copy

BUILTINS = {'ValueError': ValueError, 'isinstance': isinstance, 'type': type,
            'dict': dict, 'list': list, 'str': str, 'int': int, 'bool': bool,
            'any': any, 'all': all, 'len': len, 'sum': sum}
METHODS = {'get', 'setdefault', 'strip', 'items', 'values', 'keys', 'append', 'copy'}
NODES = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Raise,
         ast.If, ast.For, ast.Assign, ast.AugAssign, ast.Expr, ast.Pass,
         ast.Name, ast.Load, ast.Store, ast.Constant, ast.Dict, ast.List, ast.Tuple,
         ast.Set, ast.Subscript, ast.Attribute, ast.Call, ast.keyword, ast.Compare,
         ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.IfExp, ast.ListComp, ast.GeneratorExp,
         ast.DictComp, ast.comprehension, ast.And, ast.Or, ast.Not, ast.Add, ast.Sub,
         ast.USub, ast.UAdd, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
         ast.Is, ast.IsNot, ast.In, ast.NotIn)


def validate_source(source):
    tree = ast.parse(source)
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if not isinstance(body[0].value.value, str):
            raise ValueError('source_shape')
        body.pop(0)
    if len(body) != 1 or not isinstance(body[0], ast.FunctionDef):
        raise ValueError('source_shape')
    function = body[0]
    args = function.args
    if (function.name != 'summarize_usage' or function.decorator_list or function.returns
            or len(args.args) != 1 or args.args[0].arg != 'records'
            or args.args[0].annotation or args.posonlyargs or args.kwonlyargs
            or args.defaults or args.kw_defaults or args.vararg or args.kwarg
            or getattr(function, 'type_params', [])):
        raise ValueError('source_signature')
    for node in ast.walk(tree):
        if not isinstance(node, NODES):
            raise ValueError('source_syntax')
        if isinstance(node, ast.FunctionDef) and node is not function:
            raise ValueError('nested_function')
        if isinstance(node, ast.Name):
            if node.id.startswith('_') or node.id in ('exec', 'eval', 'compile', 'open'):
                raise ValueError('source_name')
            if isinstance(node.ctx, ast.Store) and node.id in BUILTINS:
                raise ValueError('builtin_shadow')
        if isinstance(node, ast.Attribute) and node.attr not in METHODS:
            raise ValueError('source_attribute')
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in BUILTINS:
                    raise ValueError('source_call')
            elif not isinstance(node.func, ast.Attribute):
                raise ValueError('source_call')
    return tree


def run_tests(source):
    """Container only: compile the accepted function with a minimal builtin map."""
    tree = validate_source(source)
    namespace = {'__builtins__': dict(BUILTINS)}
    exec(compile(tree, '<candidate>', 'exec'), namespace)
    summarize = namespace['summarize_usage']
    failed = []

    def equal(name, records, expected):
        try:
            actual = summarize(copy.deepcopy(records))
            if actual != expected:
                raise ValueError
        except BaseException:
            failed.append(name)

    def invalid(name, records):
        try:
            summarize(copy.deepcopy(records))
        except ValueError:
            return
        except BaseException:
            pass
        failed.append(name)

    equal('test_empty', [], {})
    equal('test_groups', [
        {'model': 'luna', 'input_tokens': 100, 'output_tokens': 20},
        {'model': 'sol', 'input_tokens': 8, 'output_tokens': 3},
        {'model': 'luna', 'input_tokens': 40, 'output_tokens': 5},
    ], {'luna': {'calls': 2, 'input_tokens': 140, 'output_tokens': 25, 'total_tokens': 165},
        'sol': {'calls': 1, 'input_tokens': 8, 'output_tokens': 3, 'total_tokens': 11}})
    equal('test_zero', [{'model': 'luna', 'input_tokens': 0, 'output_tokens': 0}],
          {'luna': {'calls': 1, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}})
    equal('test_groups', [{'model': ' luna ', 'input_tokens': 2, 'output_tokens': 1}],
          {' luna ': {'calls': 1, 'input_tokens': 2, 'output_tokens': 1, 'total_tokens': 3}})
    records = [{'model': 'luna', 'input_tokens': 1, 'output_tokens': 2,
                'note': 'synthetic', 'total_tokens': 999}]
    saved = copy.deepcopy(records)
    try:
        result = summarize(records)
        if records != saved or result != {'luna': {'calls': 1, 'input_tokens': 1,
                                                  'output_tokens': 2, 'total_tokens': 3}}:
            raise ValueError
    except BaseException:
        failed.append('test_no_mutation_or_extra_field_dependency')
    for records in (None, {}, 'x', (), [None], [1], [{}]):
        invalid('test_invalid_collection', records)
    for model in ('', ' ', '\t', 1, None, True):
        invalid('test_invalid_model', [{'model': model, 'input_tokens': 1, 'output_tokens': 1}])
    invalid('test_invalid_model', [{'input_tokens': 1, 'output_tokens': 1}])
    for field in ('input_tokens', 'output_tokens'):
        for bad in (-1, True, 1.0, '1', None):
            record = {'model': 'luna', 'input_tokens': 1, 'output_tokens': 2}
            record[field] = bad
            invalid('test_invalid_counts', [record])
        record = {'model': 'luna', 'input_tokens': 1, 'output_tokens': 2}
        del record[field]
        invalid('test_missing_counts', [record])
    return sorted(set(failed))
