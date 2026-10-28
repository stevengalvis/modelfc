"""Synthetic contemporaneous failure context; no native or host operations."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

PATH=Path(__file__).resolve().parents[1]/'docs/zen53/workspace-acceptance/failure_context.py'
spec=importlib.util.spec_from_file_location('workspace_context_tested',PATH)
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
CHECK='WORKSPACE_MOUNT_ALIAS'


def topology():
    return [dict(id=1,parent=0,device='8:1',destination='/',root='/stage/rootfs',options='ro',propagation=[],type='ext4'),
            dict(id=2,parent=1,device='8:1',destination='/workspace-fixture',root='/stage/workspace',options='rw',propagation=[],type='ext4'),
            dict(id=3,parent=2,device='0:9',destination='/workspace-fixture/.git',root='/',options='ro,nosuid',propagation=[],type='tmpfs')]


def failure(error):
    return 'ZEN53_REPORT_REQUIRED_OBSERVATION_FAILED class=RuntimeError errno=0 check='+CHECK+' lines=123\n'


class WorkspaceFailureContext(unittest.TestCase):
    def setUp(self):
        self.stat='123 (PRIVATE_PROCESS_NAME) S '+' '.join(['0']*18+['456'])+'\n'
        self.status='NSpid:\t123\t2\n'
        self.reader=Mock(side_effect=lambda path,**kw:io.StringIO(self.stat if path=='/proc/self/stat' else self.status))
        self.enterContext(patch.object(c,'open',self.reader,create=True))
        self.enterContext(patch.object(c,'os',NS(getpid=lambda:2,readlink=lambda path:'mnt:[789]')))
        self.enterContext(patch.object(c,'time',NS(monotonic_ns=Mock(side_effect=[100,200]))))

    def capture(self):
        before=c.workspace_context_begin(topology(),{1,2,3})
        error=RuntimeError(CHECK)
        c.workspace_context_attach(error,before)
        return error

    def test_inherited_proc_identity_and_checked_topology_bound_to_exact_failure(self):
        error=self.capture();line=c.workspace_failure_context_line(error,(CHECK,),failure)
        record=json.loads(line.split(' ',1)[1])
        self.assertEqual(record['identity'],dict(proc_pid=123,pid=2,start_ticks=456,mnt_ns=789,nspid=[123,2]))
        self.assertEqual(record['identity'],record['after_identity'])
        self.assertEqual(record['failure_sha256'],hashlib.sha256(failure(error).encode()).hexdigest())
        raw=json.dumps(topology(),sort_keys=True,separators=(',',':')).encode()
        self.assertEqual(record['checked_mounts_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(record['topology_source'],'PARSED_MOUNT_ARGUMENTS')
        self.assertEqual(record['state'],'OBSERVATION_ONLY')
        self.assertEqual(record['rows'][2][5],2)
        self.assertTrue(record['rows'][2][6]&8)
        self.assertLessEqual(len(line),2048);self.assertTrue(line.isascii())
        self.assertNotIn('PRIVATE_PROCESS_NAME',line);self.assertNotIn('/stage/',line)
        other=RuntimeError(CHECK)
        self.assertIn('state=UNKNOWN',c.workspace_failure_context_line(other,(CHECK,),failure))

    def test_pid_reuse_or_namespace_change_cannot_produce_observed_context(self):
        for change in ('start','namespace','pidmap'):
            with self.subTest(change=change):
                c.time.monotonic_ns.side_effect=[100,200]
                self.stat='123 (private) S '+' '.join(['0']*18+['456'])+'\n';self.status='NSpid: 123 2\n'
                c.os.readlink=lambda path:'mnt:[789]'
                before=c.workspace_context_begin(topology(),{1,2,3})
                if change=='start':self.stat=self.stat.replace('456','457')
                elif change=='namespace':c.os.readlink=lambda path:'mnt:[790]'
                else:self.status='NSpid: 123 3\n'
                error=RuntimeError(CHECK);c.workspace_context_attach(error,before)
                self.assertIn('state=UNKNOWN',c.workspace_failure_context_line(error,(CHECK,),failure))

    def test_capture_failure_row_overflow_and_bad_saved_values_are_unknown(self):
        rows=topology()
        for n in range(17):rows.append({**rows[1],'id':10+n,'destination':'/PRIVATE_TOKEN/'+str(n)})
        self.assertIsNone(c.workspace_context_begin(rows,set()))
        self.reader.side_effect=PermissionError('PRIVATE_TOKEN')
        self.assertIsNone(c.workspace_context_begin(topology(),set()))
        error=RuntimeError(CHECK)
        error._zen53_workspace_context={'PRIVATE_TOKEN':'secret'}
        line=c.workspace_failure_context_line(error,(CHECK,),failure)
        self.assertIn('state=UNKNOWN',line);self.assertNotIn('PRIVATE_TOKEN',line)
        self.assertEqual(c.workspace_failure_context_line(RuntimeError('PRIVATE_TOKEN'),(CHECK,),failure),'')

    def test_fixed_mask_enums_no_arbitrary_paths_and_snapshot_is_not_mutable_alias(self):
        rows=topology()
        for n,name in enumerate(('.agents','.codex','.aws','PRIVATE_TOKEN'),4):
            rows.append({**rows[2],'id':n,'destination':'/workspace-fixture/'+name})
        before=c.workspace_context_begin(rows,set(range(1,8)))
        self.assertEqual([row[5] for row in before['rows']],[0,1,2,3,4,5,6])
        rows[0]['id']=999
        error=RuntimeError(CHECK);c.workspace_context_attach(error,before)
        line=c.workspace_failure_context_line(error,(CHECK,),failure)
        self.assertNotIn('PRIVATE_TOKEN',line)
        self.assertEqual(json.loads(line.split(' ',1)[1])['rows'][0][0],1)

    def test_serialization_overflow_never_emits_truncated_observation(self):
        error=self.capture();saved=copy.deepcopy(error._zen53_workspace_context)
        saved['rows']=[[2**64-1]*4+[1,7,15,7,7] for _ in range(16)]
        identity=dict(proc_pid=2**64-1,pid=2**64-1,start_ticks=2**64-1,mnt_ns=2**64-1,nspid=[2**64-1]*8)
        saved['identity']=identity;saved['after_identity']=copy.deepcopy(identity)
        error._zen53_workspace_context=saved
        line=c.workspace_failure_context_line(error,(CHECK,),failure)
        self.assertIn('state=UNKNOWN',line);self.assertLessEqual(len(line),2048)
