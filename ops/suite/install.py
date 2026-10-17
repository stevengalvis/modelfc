"""Passive installation of an independently hash-approved bundle; no service starts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path('/usr/lib/modelfc-suite')
STATE=Path('/var/lib/modelfc-suite')
UNITS=('modelfc-suite-guard.service','modelfc-suite-worker.service')


def install(archive, expected, sha):
    if os.geteuid()!=0: raise ValueError('root operator required')
    archive=Path(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected: raise ValueError('archive hash mismatch')
    if ROOT.exists() or STATE.exists() or any((Path('/etc/systemd/system')/u).exists() for u in UNITS):
        raise ValueError('test-only resources already exist; no overwrite')
    if Path('/usr/lib').stat().st_uid!=0 or Path('/usr/lib').stat().st_mode & 0o022:
        raise ValueError('protected /usr/lib required; do not change its permissions')
    for parent in (ROOT.parent, STATE.parent, Path('/etc/systemd/system')):
        info=parent.lstat()
        if not parent.is_dir() or parent.is_symlink() or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('protected destination parent required')
    for executable in ('systemctl','systemd-analyze','busctl','journalctl','python3'):
        if not os.access('/usr/bin/'+executable,os.X_OK): raise ValueError('missing executable')
    version=subprocess.run(['/usr/bin/systemctl','--version'],capture_output=True,text=True,check=True,timeout=10)
    if version.stdout.split()[1]!='255': raise ValueError('requires accepted systemd 255')
    staged=None
    try:
        staged=Path(tempfile.mkdtemp(prefix='.modelfc-suite-',dir='/usr/lib'))
        total=0
        seen=set()
        with tarfile.open(archive,'r:gz') as bundle:
            for item in bundle:
                name=Path(item.name)
                total+=item.size
                if (name.is_absolute() or '..' in name.parts or item.name in seen or total>256*1024*1024
                        or not (item.isdir() or item.isfile()) or item.mode not in (0o644,0o755)):
                    raise ValueError('archive rejected')
                seen.add(item.name)
                output=staged/name
                if item.isdir(): output.mkdir(parents=True,exist_ok=True);output.chmod(0o755)
                else:
                    output.parent.mkdir(parents=True,exist_ok=True)
                    with output.open('xb') as stream: shutil.copyfileobj(bundle.extractfile(item),stream)
                    output.chmod(item.mode)
        value=json.loads((staged/'manifest.json').read_text())
        actual={p.relative_to(staged).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in staged.rglob('*') if p.is_file() and p.relative_to(staged).as_posix()!='manifest.json'}
        if value['version']!=1 or value['candidate']!=sha or actual!=value['files']:
            raise ValueError('manifest mismatch')
        if hashlib.sha256(Path('/usr/bin/python3').read_bytes()).hexdigest()!=value['host_python_sha256']:
            raise ValueError('host Python differs from accepted artifact; no automatic upgrade')
        subprocess.run(['/usr/bin/systemd-analyze','verify',*[str(staged/u) for u in UNITS]],
                       check=True,timeout=20,stdout=subprocess.DEVNULL)
        staged.chmod(0o755)
        os.rename(staged,ROOT);staged=None
        STATE.mkdir(mode=0o700)
        (STATE/'results').mkdir(mode=0o700)
        (STATE/'admission.lock').touch(mode=0o600)
        for unit in UNITS:
            output=Path('/etc/systemd/system')/unit
            with output.open('xb') as stream: stream.write((ROOT/unit).read_bytes())
            output.chmod(0o644)
        subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=30)
        print(json.dumps(dict(status='INSTALLED_NOT_STARTED',candidate=sha,archive_sha256=expected)))
    finally:
        if staged is not None: shutil.rmtree(staged)
    # Partial installation is deliberately left for inspection, never retried or rolled over.


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',required=True);parser.add_argument('--sha256',required=True)
    parser.add_argument('--candidate',required=True)
    args=parser.parse_args()
    install(args.archive,args.sha256,args.candidate)
