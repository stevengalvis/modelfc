from dataclasses import asdict
import hashlib
import json
import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_settings_proposal as proposal
from ops.bounded_worker.storage import StorageConfiguration, service_properties


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


class StorageSettingsProposalTest(unittest.TestCase):
    def setUp(self):
        self.settings = {'policy': '/etc/bridge/policy', 'state_root': '/var/state', 'mirror': '/var/mirror',
                         'workspace_root': '/old/work', 'evidence_root': '/var/evidence',
                         'publisher_root': '/var/publisher', 'username': 'zeno-worker',
                         'executable': '/opt/codex/bin', 'executable_sha256': 'a' * 64,
                         'reader_token_file': '/etc/bridge/reader', 'publisher_token_file': '/etc/bridge/publisher'}
        self.storage = StorageConfiguration('/srv/bounded', '/srv/images/worker.img', '/srv/bounded/workspaces',
                                            '/srv/bounded/runtime', 100, 200, 1073741824, 1063956480, 32768,
                                            134217728, 2048, '/home/worker/.codex/auth.json',
                                            '/home/worker/.codex/config.toml', 'unit')
        self.terminal = {'state': 'capacity_pending', 'identity': {'mount_id': 33, 'device': '7:0',
                         **{k: getattr(self.storage, k) for k in ('image_device', 'image_inode', 'image_bytes',
                                                                'filesystem_bytes', 'filesystem_inodes')}},
                         'fstab': {}, 'allocation_receipt': 'a' * 64,
                         'settings_sha256': hashlib.sha256(encode(self.settings)).hexdigest(),
                         'unresolved_marker_sha256': 'b' * 64, 'allocation_intent_sha256': 'c' * 64,
                         'proposed_storage': asdict(self.storage)}
        self.operator = b'synthetic opaque operator evidence'

    def call(self, settings=None, terminal=None, operator=None):
        settings = encode(self.settings) if settings is None else settings
        terminal = encode(self.terminal) if terminal is None else terminal
        operator = self.operator if operator is None else operator
        with patch.object(proposal, 'TERMINAL_SHA256', hashlib.sha256(terminal).hexdigest()), \
                patch.object(proposal, 'OPERATOR_SHA256', hashlib.sha256(self.operator).hexdigest()):
            return proposal.propose(settings, terminal, operator)

    def test_proposal_changes_only_workspace_and_storage(self):
        result = json.loads(self.call())
        self.assertEqual(result['workspace_root'], self.storage.workspace)
        self.assertEqual(result['storage'], asdict(self.storage))
        properties = service_properties(StorageConfiguration(**result['storage']),
                                        work='/srv/bounded/workspaces/task1',
                                        runtime='/srv/bounded/runtime/task1', home='/home/worker',
                                        unit='modelfc-bounded-' + 'a' * 32 + '.service')
        self.assertTrue(any('BindReadOnlyPaths=' in item for item in properties))
        for key in self.settings.keys() - {'workspace_root'}:
            self.assertEqual(result[key], self.settings[key])

    def test_settings_drift_operator_mismatch_and_existing_storage_block(self):
        for data in (encode({**self.settings, 'username': 'other'}), encode({**self.settings, 'storage': {}})):
            with self.assertRaises(ValueError):
                self.call(settings=data)
        with self.assertRaises(ValueError):
            self.call(operator=b'different')

    def test_duplicate_keys_identity_mismatch_and_unknown_fields_block(self):
        duplicate = b'{"state":"capacity_pending","state":"capacity_pending"}'
        with self.assertRaises(ValueError):
            self.call(terminal=duplicate)
        for change in ({'unknown': 1}, {'identity': {**self.terminal['identity'], 'image_inode': 201}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.call(terminal=encode({**self.terminal, **change}))

    def test_overlapping_secret_or_other_root_blocks(self):
        self.settings['reader_token_file'] = '/srv/bounded/reader'
        self.terminal['settings_sha256'] = hashlib.sha256(encode(self.settings)).hexdigest()
        with self.assertRaises(ValueError):
            self.call()

    def test_incompatible_codex_source_paths_block(self):
        changed = dict(self.terminal['proposed_storage'])
        changed['auth_source'] = '/home/worker/auth.json'
        with self.assertRaises(ValueError):
            self.call(terminal=encode({**self.terminal, 'proposed_storage': changed}))

    def test_result_must_fit_existing_bridge_settings_limit(self):
        self.settings['policy'] = '/etc/' + 'p' * 15700
        self.terminal['settings_sha256'] = hashlib.sha256(encode(self.settings)).hexdigest()
        with self.assertRaisesRegex(ValueError, 'settings_size_rejected'):
            self.call()


if __name__ == '__main__':
    unittest.main()
