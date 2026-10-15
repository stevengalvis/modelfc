"""Exact installed-baseline patch acceptance; no host/service/provider operations."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'tests/fixtures/vps/deploy_main_5c1b.py.txt'
PATCH=ROOT/'ops/vps/patches/deploy-main-5c1b-readiness.patch'
BEFORE='5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5'
AFTER='2a82ca7502724c207d4d4bd6af25f03f72ba687c868667366e6dee23f26b2db8'
COMMAND='api-readiness stevengalvis/modelfc'

class ExecReplaced(BaseException): pass

class DispatchTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
  cls.target=Path(cls.tmp.name)/'deploy_main.py'
  assert hashlib.sha256(BASE.read_bytes()).hexdigest()==BEFORE
  cls.target.write_bytes(BASE.read_bytes())
  result=subprocess.run(['patch','--batch','--fuzz=0',str(cls.target)],input=PATCH.read_bytes(),capture_output=True,timeout=5)
  assert result.returncode==0,result.stderr
  assert hashlib.sha256(cls.target.read_bytes()).hexdigest()==AFTER
  spec=importlib.util.spec_from_file_location('installed_readiness_dispatch',cls.target)
  cls.subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.subject)

 def test_only_exact_dispatch_branch_changes(self):
  before,after=BASE.read_text(),self.target.read_text()
  def without_main(source):
   tree=ast.parse(source)
   node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
   lines=source.splitlines(True)
   return ''.join(lines[:node.lineno-1]+lines[node.end_lineno:])
  self.assertEqual(without_main(before),without_main(after))
  main_before=next(n for n in ast.parse(before).body if getattr(n,'name',None)=='main')
  main_after=next(n for n in ast.parse(after).body if getattr(n,'name',None)=='main')
  del main_after.body[4]  # Only inserted branch, after existing requested assignment.
  self.assertEqual(ast.dump(main_before),ast.dump(main_after))
  self.assertFalse(hasattr(self.subject,'api_handoff'))

 def test_exact_operation_execs_fixed_noninteractive_helper_without_deploy(self):
  with patch.dict(self.subject.os.environ,{'SSH_ORIGINAL_COMMAND':COMMAND,'PYTHONPATH':'untrusted','SUDO_ASKPASS':'untrusted'},clear=True),patch.object(sys,'argv',['controller']),patch.object(self.subject.os,'execve',side_effect=ExecReplaced) as execute,patch.object(self.subject,'deploy') as deploy:
   with self.assertRaises(ExecReplaced): self.subject.main()
  deploy.assert_not_called()
  self.assertEqual(execute.call_args.args,('/usr/bin/sudo',['sudo','-n','--','/usr/bin/python3','-I','-B','/opt/modelfc-deploy/api_readiness.py'],{'PATH':'/usr/bin:/bin','LANG':'C','HOME':'/nonexistent'}))

 def test_extra_arguments_cannot_reach_helper(self):
  with patch.dict(self.subject.os.environ,{'SSH_ORIGINAL_COMMAND':COMMAND},clear=True),patch.object(sys,'argv',['controller','extra']),patch.object(self.subject.os,'execve') as execute,patch.object(self.subject,'deploy') as deploy:
   self.assertEqual(self.subject.main(),1)
  execute.assert_not_called();deploy.assert_not_called()

 def test_exec_failure_or_unexpected_return_cannot_fall_through_to_deploy(self):
  for effect in (OSError('private details'),None):
   output=io.StringIO()
   with patch.dict(self.subject.os.environ,{'SSH_ORIGINAL_COMMAND':COMMAND},clear=True),patch.object(sys,'argv',['controller']),patch.object(self.subject.os,'execve',side_effect=effect),patch.object(self.subject,'deploy') as deploy,contextlib.redirect_stdout(output):
    self.assertEqual(self.subject.main(),1)
   deploy.assert_not_called();self.assertEqual(output.getvalue(),'')

 def test_operation_variants_preserve_existing_invalid_request_behavior(self):
  for command in (COMMAND+' extra',COMMAND+'\n',' '+COMMAND,'api-readiness other/repo',COMMAND+'; reboot','api-readiness'):
   result=subprocess.run([sys.executable,'-I','-B',str(self.target)],env={'PATH':'/usr/bin:/bin','SSH_ORIGINAL_COMMAND':command},capture_output=True,timeout=5)
   self.assertEqual(result.returncode,0)
   value=json.loads(result.stdout);self.assertEqual(value['reason'],'INVALID_REQUEST')
   self.assertEqual(value['promotion_status'],'NOT_ATTEMPTED');self.assertEqual(result.stderr,b'')

 def test_normal_deploy_still_uses_exact_existing_report(self):
  sha='a'*40;value=dict.fromkeys(self.subject.FIELDS,None);output=io.StringIO()
  with patch.dict(self.subject.os.environ,{'SSH_ORIGINAL_COMMAND':'deploy stevengalvis/modelfc '+sha},clear=True),patch.object(sys,'argv',['controller']),patch.object(self.subject.os,'execve') as execute,patch.object(self.subject,'deploy',return_value=value) as deploy,contextlib.redirect_stdout(output):
   self.assertEqual(self.subject.main(),0)
  deploy.assert_called_once_with(sha);execute.assert_not_called();self.assertEqual(json.loads(output.getvalue()),value)

 def test_internal_service_routes_are_unchanged(self):
  for args,func,params in ((['--acquire-service','a'],'acquire_service',('a',)),(['--install-dependencies','a'],'run_dependency_install',('a',)),(['--run-tests'],'run_tests',())):
   with patch.dict(self.subject.os.environ,{},clear=True),patch.object(sys,'argv',['controller']+args),patch.object(self.subject,func,return_value=True) as call,patch.object(self.subject.os,'execve') as execute:
    self.assertEqual(self.subject.main(),0)
   call.assert_called_once_with(*params);execute.assert_not_called()

if __name__=='__main__': unittest.main()
