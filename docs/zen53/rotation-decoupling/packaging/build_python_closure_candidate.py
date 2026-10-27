"""Build the successor archives offline; --refresh updates derived source pins.

Does not install or execute packaged components. Historical PR212 reconstruction
remains in its separate recipe and must use that recipe's original checkout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import zen53_reconstruct_pr212_archives as recipe

BASE = Path(__file__).resolve().parents[1]
STAGE_NAME = 'zen53-python-closure-stage-offline.tar.gz'
OPERATOR_NAME = 'zen53-python-closure-operator-source-offline.tar.gz'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory):
    value = json.loads((directory/'INVENTORY.json').read_bytes())
    value['files'] = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
                      for p in sorted(directory.iterdir())
                      if p.is_file() and p.name != 'INVENTORY.json'}
    return value


def derive(gzip_sha, owner_uid=0, owner_gid=0, refresh=False):
    def update(path, data):
        if isinstance(data, dict):
            data = json.dumps(data, indent=2)+'\n'
        if refresh:
            path.write_text(data)
        elif path.read_text() != data:
            raise ValueError('STALE_DERIVED_FILE:'+str(path))

    def constants(path, values):
        text = path.read_text()
        for name, value in values.items():
            text, count = re.subn(r"^"+name+r" = '[0-9a-f]+'$",
                                  name+" = '"+value+"'", text, flags=re.M)
            if count != 1:
                raise ValueError('CONSTANT:'+name)
        update(path, text)

    stage = BASE/'stage'
    update(stage/'INVENTORY.json', inventory(stage))
    stage_inventory = sha(stage/'INVENTORY.json')
    files = recipe.checked_group(stage, stage_inventory)
    stage_archive = recipe.stage_bytes(files, gzip_sha, owner_uid, owner_gid)
    stage_sha = recipe.sha(stage_archive)
    groups = BASE/'operator-review'
    for name in recipe.OPERATOR_DIRS:
        launcher = groups/name/'launch_once.py'
        constants(launcher, {'ARCHIVE_SHA': stage_sha, 'INVENTORY_SHA': stage_inventory})
        text = launcher.read_text()
        for file in ('admission.py', 'capture_once.py', 'controller.py', 'evidence.py',
                     'operator_entry.py', 'owner_adapter.py', 'owner_context_v4.py',
                     'restricted_launcher.py', 'runtime_dependencies.json',
                     'runtime_source_manifest.json'):
            text, count = re.subn(r"('"+re.escape(file)+r"': ')[0-9a-f]+(')",
                                  lambda m: m[1]+sha(stage/file)+m[2], text)
            if count != 1:
                raise ValueError('TOOL_PIN:'+file)
        update(launcher, text)
    operator = groups/recipe.OPERATOR_DIRS[0]
    constants(operator/'operator_once.py', {
        'PREFLIGHT_SHA': sha(operator/'zen53-preparation-preflight.py'),
        'LAUNCHER_SHA': sha(operator/'launch_once.py'),
        'PACKAGE_ARCHIVE_SHA': stage_sha, 'PACKAGE_INVENTORY_SHA': stage_inventory})
    constants(operator/'root_operator.py', {'OPERATOR_SHA': sha(operator/'operator_once.py')})
    for name in recipe.OPERATOR_DIRS:
        update(groups/name/'INVENTORY.json', inventory(groups/name))
    payload = {name: recipe.checked_group(groups/name, sha(groups/name/'INVENTORY.json'))
               for name in recipe.OPERATOR_DIRS}
    operator_archive = recipe.operator_bytes(payload)
    manifest = {
        'status': 'OFFLINE_SUCCESSOR_NOT_INSTALLED_NOT_LIVE_ACCEPTED',
        'predecessor_commit': '0a2b1771c1770193f95d917ace1bcdd4a944598f',
        'stage_archive': {'filename': STAGE_NAME, 'bytes': len(stage_archive),
                          'sha256': stage_sha, 'inventory_sha256': stage_inventory,
                          'member_count': len(files)+1},
        'operator_distribution': {'filename': OPERATOR_NAME, 'bytes': len(operator_archive),
                                  'sha256': recipe.sha(operator_archive),
                                  'groups': {name: {'inventory_sha256': sha(groups/name/'INVENTORY.json'),
                                                    'members': sorted(payload[name])}
                                             for name in recipe.OPERATOR_DIRS}},
    }
    update(BASE/'SOURCE_TREE_MANIFEST.json', manifest)
    return {STAGE_NAME: stage_archive, OPERATOR_NAME: operator_archive}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gzip-sha256', required=True)
    parser.add_argument('--gzip-owner-uid', type=int, default=0)
    parser.add_argument('--gzip-owner-gid', type=int, default=0)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    archives = derive(args.gzip_sha256, args.gzip_owner_uid, args.gzip_owner_gid, args.refresh)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        for name, raw in archives.items():
            with (args.output/name).open('xb') as out:
                out.write(raw)
    for name, raw in archives.items():
        print(hashlib.sha256(raw).hexdigest(), len(raw), name)
