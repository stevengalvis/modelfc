import dataclasses
import hashlib
import unittest
from unittest.mock import patch
from ops.bounded_worker import linear_patch_trial as trial, linear_patch_delivery as delivery
from ops.bounded_worker import linear_remote_entry as host, linear_coding_runtime as runtime
from ops.bounded_worker.linear_coding_intake import _canonical, _task, binding
from tests import test_linear_remote_entry as fixture
from tests.test_linear_coding_intake import uid
from tests.test_linear_patch_export import PATCH


class TrialTests(unittest.TestCase):
    def setUp(self):
        fixture.RemoteEntryTests.setUp(self)
        delivery.provision_artifacts(self.root); trial.provision_trial(self.root)
        self.value['artifact_policy']='/disclosure'
        self.grant=dict(version=1,enabled=True,scope=dataclasses.asdict(self.policy.policy),viewer=uid(4),
                        paths=['docs/example.md'],expires=300,max_outputs=2,channel_sha256=trial._fingerprint(self.policy))
        self.stack.enter_context(patch.object(trial,'protected_json',side_effect=lambda _: self.grant))
        self.artifacts=[]
        outer=self
        class API:
            def __init__(self,*a,**kw): pass
            def call(self,query,args):
                if 'body' not in args: return {}
                outer.artifacts.append(args['body'])
                return {'commentCreate':dict(success=True,comment=dict(id=uid(9),body=args['body'],issue=dict(id=uid(3)),user=dict(id=uid(4))))}
        self.stack.enter_context(patch.object(host,'LinearAPI',API))
        class Destination:
            def __init__(self,*args,**kw): self.policy=outer.policy.policy;self.viewer_id=uid(4)
            def query(self,q): return q
            def check(self,v): pass
        self.stack.enter_context(patch.object(host,'Destination',Destination))
        def export(req,config,path,*,_policy_loader=None):
            _policy_loader()
            return _canonical(dict(version=1,operation='coding-patch',job_id='one',task_id=_task(req)[1].task_id,
                request_comment_id=req.comment,request_sha256=binding(req),diff_sha256=hashlib.sha256(PATCH).hexdigest(),
                paths=['docs/example.md'],empty=False,tests='not recorded',patch=PATCH.decode()))
        self.export=self.stack.enter_context(patch.object(delivery,'prepare_export',side_effect=export))

    def run_host(self,now=100): return host.run('/config',clock=lambda:now)

    def test_auto_hook_one_output_no_deadlock_restart(self):
        self.assertEqual(self.run_host()['artifact'],'posted')
        self.assertEqual(self.run_host(161)['status'],'idle')
        self.assertEqual(len(self.artifacts),1)
        self.executor.run.assert_called_once()

    def test_expired_scope_never_executes_or_sends(self):
        self.grant['expires']=100
        self.assertEqual(self.run_host()['status'],'blocked')
        self.executor.run.assert_not_called();self.assertFalse(self.artifacts)

    def test_wrong_file_or_issue_rejected(self):
        for change in ({'paths':['other.py']},{'scope':dict(self.grant['scope'],issue=uid(99))},{'max_outputs':4}):
            with self.subTest(change=change),patch.object(trial,'protected_json',return_value=self.grant|change):
                with self.assertRaises(ValueError): trial.load_grant('/disclosure',self.policy,100)

    def test_no_retroactive_export(self):
        del self.value['artifact_policy']
        self.assertEqual(self.run_host()['status'],'completed')
        self.value['artifact_policy']='/disclosure'
        self.assertEqual(self.run_host(161)['status'],'idle')
        self.assertFalse(self.artifacts)

    def test_quota_enrollment_and_policy_change(self):
        self.grant['max_outputs']=1
        self.assertEqual(self.run_host()['artifact'],'posted')
        req=dataclasses.replace(self.req,comment=uid(22))
        obj=__import__('json').loads(req.body);obj['job_id']='two';req=dataclasses.replace(req,body=_canonical(obj))
        with runtime.receipt_store(self.root) as db:
            with self.assertRaisesRegex(ValueError,'quota'): trial.enroll(db,req,dataclasses.replace(self.command,comment=uid(23)),'/disclosure',self.policy,110)
            self.grant['max_outputs']=2
            with self.assertRaisesRegex(ValueError,'changed'): trial.enroll(db,req,self.command,'/disclosure',self.policy,110)

    def test_expiry_after_completion_stops_artifact(self):
        # Gate uses repeated clock checks; only artifact's grant check expires.
        original=trial.finish
        def expired(*args):
            self.grant['enabled']=False
            return original(*args)
        with patch.object(trial,'finish',side_effect=expired):
            self.assertEqual(self.run_host()['artifact'],'blocked')
        self.assertFalse(self.artifacts)

    def test_artifact_recovery_after_status_without_rerun(self):
        with patch.object(trial,'finish',return_value='blocked'):
            self.assertEqual(self.run_host()['artifact'],'blocked')
        self.assertEqual(self.run_host(161)['artifact'],'posted')
        self.executor.run.assert_called_once()
        self.assertEqual(len(self.artifacts),1)

    def test_removing_hook_does_not_repeat_completed_pair(self):
        with patch.object(trial,'finish',return_value='blocked'):
            self.assertEqual(self.run_host()['artifact'],'blocked')
        del self.value['artifact_policy']
        self.assertEqual(self.run_host(161)['status'],'idle')
        self.executor.run.assert_called_once()
        self.assertFalse(self.artifacts)
