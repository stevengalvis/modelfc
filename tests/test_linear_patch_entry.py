from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from tests import test_linear_patch_delivery as fixture
from tests.test_linear_coding_intake import uid
from ops.bounded_worker import linear_patch_entry as entry


class EntryTests(unittest.TestCase):
    def setUp(self):
        fixture.DeliveryTests.setUp(self)
        self.policy.update(request_sha256=entry.binding(self.req))
        self.loaded = ({'channel_policy':'/channel','token_file':'/token','members':[], 'audience':None},
                       {'state_root':str(self.root),'viewer':uid(4)},SimpleNamespace(policy=self.dest.policy))
        self.settings = self.enterContext(patch.object(entry,'settings',return_value=self.loaded))
        self.enterContext(patch.object(entry,'protected_json',return_value=self.policy))
        self.enterContext(patch.object(entry,'_validate_host'))
        self.enterContext(patch.object(entry,'protected_bytes',return_value=b'token'))
        self.executor = self.enterContext(patch.object(entry,'_executor',return_value=Mock(config=None)))
        self.enterContext(patch.object(entry,'Destination',return_value=self.dest))
        self.enterContext(patch.object(entry.LinearAPI,'call',side_effect=self.api.call.side_effect))

    def test_explicit_completed_export_no_worker(self):
        self.assertEqual(entry.run('/host','/disclosure'),'posted')
        self.executor.return_value.run.assert_not_called()

    def test_disabled_before_credentials_or_executor(self):
        self.policy['enabled']=False
        self.assertEqual(entry.run('/host','/disclosure'),'blocked')
        self.executor.assert_not_called()

    def test_host_change_blocks_before_send(self):
        self.settings.side_effect=[self.loaded, ValueError('changed')]
        self.assertEqual(entry.run('/host','/disclosure'),'blocked')
        self.executor.assert_not_called()
