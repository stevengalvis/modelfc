"""Build a deterministic, exact-source, offline installation bundle in CI."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def git(source, *args, data=None):
    return subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
                           '-C', str(source), *args], input=data, capture_output=True,
                          check=True, env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1'}).stdout


def runner_files(diagnostic=False):
    """Render one of two fixed reviewed policies; no caller-supplied duration."""
    result = {}
    for name in ('guard.py', 'worker.py', 'modelfc-suite-guard.service', 'modelfc-suite-worker.service'):
        text = (Path(__file__).parent/name).read_text()
        if diagnostic:
            if name.endswith('.py'):
                if text.count('DIAGNOSTIC = False') != 1: raise ValueError('mode marker changed')
                text = text.replace('DIAGNOSTIC = False', 'DIAGNOSTIC = True')
            else:
                text = text.replace('modelfc-suite', 'modelfc-suite-diag600')
                name = name.replace('modelfc-suite', 'modelfc-suite-diag600')
                if name.endswith('-guard.service'):
                    text = text.replace('guard.py supervise', 'guard.py diagnostic-supervise').replace('RuntimeMaxSec=420', 'RuntimeMaxSec=720')
                else:
                    text = text.replace('worker.py\n', 'worker.py --diagnostic-600\n').replace('TimeoutStartSec=330', 'TimeoutStartSec=630')
                    text = text.replace('-/var/lib/modelfc-suite-diag600', '-/var/lib/modelfc-suite')
                    text = text.replace('-/etc/modelfc\n', '-/etc/modelfc -/var/lib/modelfc-suite-diag600\n')
        result[name] = text.encode()
    return result


def build(source, sha, runtime, destination, diagnostic=False):
    source, runtime, destination = map(Path, (source, runtime, destination))
    if git(source, 'rev-parse', 'HEAD').decode().strip() != sha:
        raise ValueError('candidate head mismatch')
    if git(source, 'show', sha + ':requirements-deploy.lock') != (Path(__file__).parents[2]/'requirements-deploy.lock').read_bytes():
        raise ValueError('candidate requires a separately reviewed runtime')
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        target = stage/'source'; target.mkdir()
        with tarfile.open(fileobj=io.BytesIO(git(source, 'archive', sha)), mode='r:') as archive:
            for entry in archive:
                path = Path(entry.name)
                if path.is_absolute() or '..' in path.parts or not (entry.isdir() or entry.isfile()):
                    raise ValueError('non-regular candidate source')
                output = target/path
                if entry.isdir(): output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(archive.extractfile(entry).read())
                    output.chmod(0o755 if entry.mode & 0o111 else 0o644)
        # Only this commit/tree/blob graph, no hooks/config/remotes from checkout.
        git(target, 'init', '--template=', '--quiet')
        objects = git(source, 'rev-list', '--objects', '--no-object-names', sha + '^{tree}') + sha.encode() + b'\n'
        packed = git(source, 'pack-objects', '--stdout', '--threads=1', '--window=0', data=objects)
        git(target, 'index-pack', '--stdin', data=packed)
        (target/'.git/HEAD').write_text(sha + '\n')
        (target/'.git/shallow').write_text(sha + '\n')
        git(target, 'read-tree', sha)
        # A runtime prepared with /usr/bin/python3 -m venv --copies and the exact lock.
        if not (runtime/'bin/python3').is_file() or (runtime/'bin/python3').is_symlink():
            raise ValueError('runtime must use --copies')
        for path in runtime.rglob('*'):
            relative = path.relative_to(runtime)
            if '__pycache__' in relative.parts or relative.parts[0] == 'lib64': continue
            if path.is_symlink(): raise ValueError('unexpected runtime symlink')
            if path.is_file():
                output = stage/'runtime'/relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output)
                if relative.as_posix() == 'pyvenv.cfg':
                    output.write_text('\n'.join(line for line in output.read_text().splitlines() if not line.startswith('command =')) + '\n')
                output.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        for name, content in runner_files(diagnostic).items():
            (stage/name).write_bytes(content)
        files = {p.relative_to(stage).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(stage.rglob('*')) if p.is_file()}
        manifest = dict(version=1,candidate=sha,files=files,
                        host_python_sha256=hashlib.sha256((runtime/'bin/python3').read_bytes()).hexdigest())
        if diagnostic: manifest['diagnostic_seconds'] = 600
        (stage/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n')
        destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('wb') as stream, gzip.GzipFile(filename='',mode='wb',fileobj=stream,mtime=0) as zipped, tarfile.open(fileobj=zipped,mode='w') as archive:
            for path in sorted(stage.rglob('*')):
                info=archive.gettarinfo(str(path),arcname=path.relative_to(stage).as_posix())
                info.uid=info.gid=info.mtime=0; info.uname=info.gname=''
                info.mode=0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644
                if path.is_file():
                    with path.open('rb') as content: archive.addfile(info,content)
                else: archive.addfile(info)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True);parser.add_argument('--sha',required=True)
    parser.add_argument('--runtime',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--diagnostic-600',action='store_true')
    args=parser.parse_args()
    print(build(args.source,args.sha,args.runtime,args.output,args.diagnostic_600)+'  '+Path(args.output).name)
