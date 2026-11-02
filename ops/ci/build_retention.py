"""Reconstruct the pinned retention controller locally; never install or run it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

PINS = {
    "input_sha256": "26b7acb8ca99268e2710cf5cc89f82f395087ba1d69a8c98d1fcc6b5e66e797c",
    "patch_sha256": "ed28204efdbd260ae38b3f143edae92dfd88080292953253944454e669dd35b1",
    "output_sha256": "9d3e94dac40fae1285f13fe522efe38bce559554d58356078c2ce874cf3a3be5"
}


def build(source, output):
    manifest = json.loads((source / 'ops/vps/patches/deploy-main-26b7-retention.json').read_text())
    patch = source / 'ops/vps/patches/deploy-main-26b7-retention.patch'
    if manifest != PINS or hashlib.sha256(patch.read_bytes()).hexdigest() != PINS['patch_sha256']:
        raise ValueError('PATCH_IDENTITY')
    spec = importlib.util.spec_from_file_location('admission_builder', source / 'ops/ci/build_admission.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'build'
        target = root / 'ops/vps/deploy_main.py'
        target.parent.mkdir(parents=True)
        builder.build(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != PINS['input_sha256']:
            raise ValueError('INPUT_IDENTITY')
        for args in (['--check'], []):
            subprocess.run(['git', 'apply', *args, str(patch)], cwd=root, check=True,
                           capture_output=True, timeout=30)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != PINS['output_sha256']:
            raise ValueError('OUTPUT_IDENTITY')
        compile(data, 'retention-compatibility-controller', 'exec')
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
