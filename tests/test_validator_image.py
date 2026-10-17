"""Offline build-input contract; actual rootless execution is a separate opt-in suite."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

OPS=Path(__file__).resolve().parents[1]/'ops/vps'
spec=importlib.util.spec_from_file_location('prepare_image_test',OPS/'prepare_validator_image.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


class ImageInputsTests(unittest.TestCase):
    def test_complete_pinned_closure_matches_established_deployment_lock(self):
        manifest=json.loads((OPS/'validator-image-inputs.json').read_bytes())
        names={'pydantic','pydantic-core','annotated-types','typing-extensions','typing-inspection'}
        self.assertEqual({p['name'] for p in manifest['packages']},names)
        lock=(OPS/'requirements-validator.lock').read_text()
        established=(OPS.parents[1]/'requirements-deploy.lock').read_text()
        for p in manifest['packages']:
            line=p['name']+'=='+p['version']+' --hash=sha256:'+p['sha256']
            self.assertIn(line,lock);self.assertIn(line,established)
            self.assertTrue(p['url'].startswith('https://files.pythonhosted.org/'))
            self.assertTrue(p['filename'].endswith('.whl'))
        recipe=(OPS/'Containerfile').read_text()
        self.assertEqual(recipe.count('FROM '+manifest['base']),2)
        self.assertIn('--no-index',recipe);self.assertIn('--require-hashes',recipe)
        self.assertIn('--no-compile',recipe);self.assertNotIn('PYTHON_BASE',recipe)
        self.assertEqual(manifest['harness_sha256'],hashlib.sha256((OPS/'validate_capture.py').read_bytes()).hexdigest())

    def fixture(self, root):
        source=root/'source';source.mkdir();wheels=root/'wheels';wheels.mkdir()
        for name in ('Containerfile','requirements-validator.lock','validate_capture.py'):
            (source/name).write_text(name)
        packages=[]
        for i in range(5):
            raw=('verified wheel '+str(i)).encode();name=str(i)+'.whl';(wheels/name).write_bytes(raw)
            packages.append(dict(filename=name,sha256=hashlib.sha256(raw).hexdigest()))
        manifest=dict(platform='linux/amd64',packages=packages,source_date_epoch=100,
            base='docker.io/library/python@sha256:'+'a'*64,
            harness_sha256=hashlib.sha256((source/'validate_capture.py').read_bytes()).hexdigest())
        (source/'validator-image-inputs.json').write_text(json.dumps(manifest))
        return source,wheels

    def test_context_is_new_bounded_verified_and_timestamped(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source,wheels=self.fixture(root)
            with patch.object(prepare,'SOURCE',source):
                result=prepare.prepare(root/'context',wheels)
                self.assertEqual(len(result['wheel_hashes']),5)
                self.assertEqual((root/'context/validate_capture.py').stat().st_mtime,100)
                self.assertEqual(len(list((root/'context/wheels').iterdir())),5)
                with self.assertRaises(FileExistsError):prepare.prepare(root/'context',wheels)

    def test_corrupt_wheel_symlink_or_changed_harness_rejected_before_context_creation(self):
        for kind in ('corrupt','symlink','harness'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as t:
                root=Path(t);source,wheels=self.fixture(root)
                if kind=='corrupt':(wheels/'0.whl').write_text('changed')
                elif kind=='symlink':
                    (wheels/'0.whl').rename(wheels/'alias');(wheels/'0.whl').symlink_to(wheels/'alias')
                else:(source/'validate_capture.py').write_text('unreviewed')
                with patch.object(prepare,'SOURCE',source),self.assertRaises(ValueError):
                    prepare.prepare(root/'context',wheels)
                self.assertFalse((root/'context').exists())


if __name__=='__main__':unittest.main()
