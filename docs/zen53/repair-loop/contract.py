"""Pure fixed-family proposal validation, never execution or provenance proof."""
import ast
import hashlib
import json

BEFORE = b'def add(a, b):\n    return a - b\n'
BASE_SHA256 = hashlib.sha256(BEFORE).hexdigest()
CANDIDATES = {op: ('def add(a, b):\n    return a ' + op + ' b\n').encode('ascii') for op in ('+', '-', '*')}
ERROR = 'REPAIR_LOOP_REJECTED'


def need(ok):
    if not ok:
        raise ValueError(ERROR)


def hex64(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result)
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def loads(raw, limit=4096):
    need(type(raw) is bytes and 0 < len(raw) <= limit)
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda value: need(False))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(ERROR) from None


def bindings(value):
    need(type(value) is dict and set(value) == {'loop_id', 'attempt', 'request_id', 'feedback_sha256'})
    need(type(value['attempt']) is int and value['attempt'] in (1, 2))
    need(all(hex64(value[key]) for key in ('loop_id', 'request_id', 'feedback_sha256')))


def validate_proposal(raw, expected):
    """Expected bindings are trusted caller input, not extracted from model text."""
    bindings(expected)
    value = loads(raw)
    need(type(value) is dict and set(value) == {
        'version', 'loop_id', 'attempt', 'request_id', 'feedback_sha256',
        'base_sha256', 'path', 'replacement'})
    need(type(value['version']) is int and value['version'] == 1)
    bindings({key: value[key] for key in expected})
    need(all(value[key] == item for key, item in expected.items()))
    need(value['base_sha256'] == BASE_SHA256 and value['path'] == 'calc.py')
    need(type(value['replacement']) is str)
    selected = [data for data in CANDIDATES.values() if value['replacement'] == data.decode('ascii')]
    need(len(selected) == 1)
    # Parsing only: no compile/eval/exec of candidate code on the coordinator host.
    tree = ast.parse(value['replacement'], mode='exec')
    need(len(tree.body) == 1 and type(tree.body[0]) is ast.FunctionDef)
    function = tree.body[0]
    need(function.name == 'add' and not function.decorator_list and function.returns is None
         and len(function.body) == 1 and type(function.body[0]) is ast.Return)
    args = function.args
    need([arg.arg for arg in args.args] == ['a', 'b'] and all(arg.annotation is None for arg in args.args)
         and not args.posonlyargs and not args.kwonlyargs and not args.defaults
         and not args.kw_defaults and args.vararg is None and args.kwarg is None)
    expr = function.body[0].value
    need(type(expr) is ast.BinOp and type(expr.op) in (ast.Add, ast.Sub, ast.Mult)
         and type(expr.left) is ast.Name and expr.left.id == 'a'
         and type(expr.right) is ast.Name and expr.right.id == 'b')
    return dict(value, response_sha256=hashlib.sha256(raw).hexdigest(),
                replacement_sha256=hashlib.sha256(selected[0]).hexdigest())
