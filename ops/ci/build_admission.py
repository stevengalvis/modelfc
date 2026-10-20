"""Reconstruct a pinned compatibility patch locally; never install or run it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

INPUT = '08f7b8df9f7807249a2057fc72e64ae0326bce85e9ee633c386d7f1bae37f785'


def build(source, output):
    spec = importlib.util.spec_from_file_location('budget_builder', source / 'ops/ci/test_progress_systemd.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    manifest = json.loads((source / 'ops/vps/patches/deploy-main-08f7-admission.json').read_text())
    patch = source / 'ops/vps/patches/deploy-main-08f7-admission.patch'
    if (set(manifest) != {'input_sha256', 'patch_sha256', 'output_sha256'}
            or manifest['input_sha256'] != INPUT
            or hashlib.sha256(patch.read_bytes()).hexdigest() != manifest['patch_sha256']):
        raise ValueError('PATCH_IDENTITY')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'build'
        target = builder.build(source, root)
        if hashlib.sha256(target.read_bytes()).hexdigest() != INPUT:
            raise ValueError('INPUT_IDENTITY')
        for args in (['--check'], []):
            subprocess.run(['git', 'apply', *args, str(patch)], cwd=root, check=True, capture_output=True)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['output_sha256']:
            raise ValueError('OUTPUT_IDENTITY')
        compile(data, 'compatibility-controller', 'exec')
        # Exclusive local artifact creation; no installation, chmod, or execution.
        with output.open('xb') as stream:
            stream.write(data)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.source.resolve(), args.output)
