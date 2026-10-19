"""Prepare a separate Caddy candidate; never install, reload, or contact a service."""
import argparse
import hashlib
from pathlib import Path
import re

OLD = '{http.request.uri.query} == "" || {http.request.uri.query} == "competition=E1"'
EXPRESSION = Path(__file__).resolve().parents[2] / 'deploy/proposals/btts-performance-query.expression'


def patch_config(raw, expected_sha256):
    """Replace one diagnosed expression, preserving every other input byte."""
    if (len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256
            or b'\x00' in raw):
        raise ValueError('INPUT_PIN_REJECTED')
    # Match only the diagnosed named block, with its exact path/method/directive
    # order. No extra directives, comments, or duplicate matcher definitions.
    if len(re.findall(rb'(?m)^[ \t]*@btts_performance\b', raw)) != 1:
        raise ValueError('MATCHER_REJECTED')
    block = re.compile(
        rb'(?m)^[ \t]*@btts_performance[ \t]+\{\r?\n'
        rb'[ \t]+path /api/v1/research/btts/performance[ \t]*\r?\n'
        rb'[ \t]+method GET[ \t]*\r?\n'
        rb'[ \t]+expression `(?P<expression>' + re.escape(OLD.encode()) + rb')`[ \t]*\r?\n'
        rb'[ \t]*\}')
    match = block.search(raw)
    if not match:
        raise ValueError('MATCHER_REJECTED')
    replacement = EXPRESSION.read_bytes().rstrip(b'\n')
    if b'\n' in replacement or b'`' in replacement:
        raise ValueError('PATCH_REJECTED')
    start, end = match.span('expression')
    return raw[:start] + replacement + raw[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.input.open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    candidate = patch_config(raw, args.sha256)
    # Exclusive creation: never overwrite the source, an existing candidate or a
    # symlink. Operator installation remains a separate, explicitly approved step.
    with args.output.open('xb') as output:
        output.write(candidate)
    print('candidate_sha256=' + hashlib.sha256(candidate).hexdigest())


if __name__ == '__main__':
    main()
