import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ops.bounded_worker import linear_patch_delivery as delivery, linear_coding_runtime as rt
from ops.bounded_worker.linear_coding_intake import _canonical, _task, binding, snapshot, _safe
from tests.test_linear_coding_intake import request, uid
from tests.test_linear_coding_runtime import fixture_dir, try_lock
from tests.test_linear_patch_export import PATCH


def crash_artifact(root, req, policy, body):
    dest = Mock(viewer_id=uid(4))
    dest.policy = SimpleNamespace(**{k:getattr(req,k) for k in ('workspace','team','issue','actor')})
    api = Mock()
    api.call.side_effect = lambda *_: os._exit(29)
    with patch.object(rt, 'protected_dir', fixture_dir), patch.object(delivery, 'protected_json', return_value=policy), patch.object(delivery, 'prepare_export', return_value=body):
        delivery.deliver_artifact(root, req, None, '/policy', api, dest)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.object(rt, 'protected_dir', fixture_dir))
        rt.provision_store(self.root)
        delivery.provision_artifacts(self.root)
        self.req = request()
        self.task = _task(self.req)[1]
        with rt.receipt_store(self.root) as db:
            db.execute('INSERT INTO linear_coding_jobs VALUES (?,?,?,?,?,?)', ('one', self.req.comment,binding(self.req),snapshot(self.req),self.task.task_id,'completed'))
            db.commit()
        self.policy = dict(viewer=uid(4), enabled=True)
        self.read = self.enterContext(patch.object(delivery, 'protected_json', return_value=self.policy))
        self.body = _canonical(dict(version=1,operation='coding-patch',job_id='one',task_id=self.task.task_id,request_comment_id=self.req.comment,request_sha256=binding(self.req),diff_sha256=hashlib.sha256(PATCH).hexdigest(),paths=['docs/example.md'],empty=False,tests='not recorded',patch=PATCH.decode()))
        self.export = self.enterContext(patch.object(delivery, 'prepare_export', return_value=self.body))
        self.dest = Mock(viewer_id=uid(4))
        self.dest.policy = SimpleNamespace(**{k:getattr(self.req,k) for k in ('workspace','team','issue','actor')})
        self.api = Mock()
        self.api.call.side_effect = lambda query, args: ({'commentCreate': {'success':True,'comment':{'id':uid(9),'body':args['body'],'issue':{'id':args['issue']},'user':{'id':uid(4)}}}} if 'body' in args else {})

    def run_send(self):
        return delivery.deliver_artifact(self.root,self.req,None,'/policy',self.api,self.dest)

    def test_post_and_restart_duplicate(self):
        self.assertEqual(self.run_send(),'posted')
        self.assertEqual(self.run_send(),'duplicate')
        self.assertEqual(self.api.call.call_count,2)
        with rt.receipt_store(self.root) as db: self.assertTrue(_safe(db))

    def test_uncertain_send_holds_all_modes(self):
        self.api.call.side_effect = [{},TimeoutError()]
        self.assertEqual(self.run_send(),'blocked')
        self.assertEqual(self.run_send(),'blocked')
        self.assertEqual(self.api.call.call_count,2)
        with rt.receipt_store(self.root) as db: self.assertFalse(_safe(db))

    def test_changed_evidence(self):
        self.export.side_effect = [self.body,ValueError('changed')]
        self.assertEqual(self.run_send(),'blocked')
        self.assertEqual(self.api.call.call_count,1)
        with rt.receipt_store(self.root) as db: self.assertFalse(_safe(db))

    def test_revocation_after_audience_check(self):
        self.read.side_effect = [self.policy,self.policy,dict(self.policy,enabled=False)]
        self.assertEqual(self.run_send(),'blocked')
        self.assertEqual(self.api.call.call_count,1)

    def test_wrong_destination(self):
        self.dest.policy.issue=uid(8)
        self.assertEqual(self.run_send(),'blocked')
        self.api.call.assert_not_called()

    def test_wrong_echo_and_uuid(self):
        self.api.call.side_effect = [{},{'commentCreate':{'success':True,'comment':{'body':'wrong','issue':{'id':uid(3)},'user':{'id':uid(4)},'id':42}}}]
        self.assertEqual(self.run_send(),'blocked')
        self.assertEqual(self.run_send(),'blocked')

    def test_crash_before_io_is_durable(self):
        ctx=multiprocessing.get_context('spawn')
        proc=ctx.Process(target=crash_artifact,args=(str(self.root),self.req,self.policy,self.body))
        proc.start();proc.join(10)
        self.assertEqual(proc.exitcode,29)
        self.assertEqual(self.run_send(),'blocked')
        self.api.call.assert_not_called()

    def test_common_cross_process_lock(self):
        ctx=multiprocessing.get_context('spawn'); output=ctx.Queue()
        with rt.receipt_store(self.root):
            proc=ctx.Process(target=try_lock,args=(str(self.root),output))
            proc.start();proc.join(10)
            self.assertEqual(output.get(timeout=2),'busy')
            self.assertEqual(self.run_send(),'busy')
        self.api.call.assert_not_called()
