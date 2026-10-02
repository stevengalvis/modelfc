"""In-process and stdio MCP contract over synthetic, offline research evidence."""

import asyncio
import json
from importlib.util import find_spec
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from unittest.mock import mock_open
import importlib.util

from modelfc import corner_research as domain
from modelfc import ledger_storage
from tests import test_corner_prospective as pilot

if find_spec('mcp'):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters
    from modelfc import private_research_mcp as adapter


@unittest.skipUnless(find_spec('mcp'), 'optional private MCP dependencies not installed in production release')
class PrivateResearchMcpTests(unittest.IsolatedAsyncioTestCase):
    setUp = pilot.PilotTests.setUp
    sleep = pilot.PilotTests.sleep
    http = pilot.PilotTests.http
    control = pilot.PilotTests.control
    run_pilot = pilot.PilotTests.run_pilot
    capture_paths = pilot.PilotTests.capture_paths
    after_kickoff = pilot.PilotTests.after_kickoff

    async def test_discovery_schemas_and_annotations(self):
        async with Client(adapter.build_server(self.state)) as client:
            listed = await client.list_tools()
        self.assertEqual([t.name for t in listed.tools], [
            'create_research_snapshot', 'research_summary', 'segment_comparison', 'inspect_fixture'])
        for tool in listed.tools:
            self.assertTrue(tool.description)
            self.assertEqual(tool.input_schema['additionalProperties'], False)
            self.assertIsNone(tool.output_schema)
            self.assertFalse(tool.annotations.open_world_hint)
            self.assertFalse(tool.annotations.destructive_hint)
            self.assertEqual(tool.annotations.read_only_hint, tool.name != 'create_research_snapshot')
            self.assertNotIn('path', tool.input_schema['properties'])
            self.assertNotIn('command', tool.input_schema['properties'])
        self.assertFalse(listed.tools[0].annotations.idempotent_hint)
        self.assertEqual(listed.tools[0].input_schema['properties'], {})
        self.assertEqual(listed.tools[2].input_schema['properties']['segment']['enum'], ['venue', 'venue_history'])
        for tool in listed.tools[1:]:
            self.assertEqual(tool.input_schema['properties']['snapshot_id']['maxLength'], 32)

    async def test_empty_initialized_snapshot_and_followups_reuse_id(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            (state / 'prospective').mkdir(parents=True)
            (state / 'prospective' / 'runner.lock').touch()
            async with Client(adapter.build_server(state)) as client:
                created = await client.call_tool('create_research_snapshot', {})
                self.assertFalse(created.is_error)
                snapshot = json.loads(created.content[0].text)
                self.assertEqual(snapshot['competition'], 'E1')
                self.assertEqual(snapshot['model_family'], 'team_corners')
                self.assertEqual(snapshot['coverage']['predictions_included'], 0)
                self.assertEqual(snapshot['coverage']['settled_predictions'], 0)
                self.assertEqual(snapshot['coverage']['paired_champion_shadow_predictions'], 0)
                self.assertEqual(snapshot['warnings'], ['NO_SETTLED_PAIRED_EVIDENCE'])
                identity = snapshot['snapshot_id']
                before = list((state / 'research-snapshots').glob('*.json'))
                for tool, extra in (('research_summary', {}), ('segment_comparison', {'segment':'venue'}),
                                    ('segment_comparison', {'segment':'venue_history'})):
                    result = await client.call_tool(tool, {'snapshot_id': identity, **extra})
                    self.assertFalse(result.is_error)
                    self.assertEqual(json.loads(result.content[0].text)['snapshot_id'], identity)
                self.assertEqual(before, list((state / 'research-snapshots').glob('*.json')))
                self.assertEqual(len(before), 1)
                self.assertFalse((state / '.lock').exists())

    async def test_populated_exact_domain_parity_and_private_market_fields(self):
        self.run_pilot()
        count = len(self.calls)
        before = {p: p.read_bytes() for p in self.state.rglob('*.json')}
        async with Client(adapter.build_server(self.state)) as client:
            created = json.loads((await client.call_tool('create_research_snapshot', {})).content[0].text)
            identity = created['snapshot_id']
            self.assertEqual(created['coverage']['paired_champion_shadow_predictions'], 1)
            self.assertEqual(created['coverage']['settled_predictions'], 0)
            summary = await client.call_tool('research_summary', {'snapshot_id': identity})
            self.assertIsNone(summary.structured_content)
            self.assertEqual(json.loads(summary.content[0].text), domain.research_summary(self.state, identity))
            for dimension in domain.SEGMENTS:
                segment = await client.call_tool('segment_comparison', {'snapshot_id': identity, 'segment': dimension})
                self.assertEqual(json.loads(segment.content[0].text),
                                 domain.segment_comparison(self.state, identity, dimension))
            prediction_id = next(iter((self.state / 'predictions').glob('*.json'))).stem
            detail = await client.call_tool('inspect_fixture', {'snapshot_id': identity, 'prediction_id': prediction_id})
            self.assertEqual(json.loads(detail.content[0].text), domain.inspect_fixture(self.state, identity, prediction_id))
            self.assertIn('bookmaker', json.dumps(json.loads(detail.content[0].text)))
            self.assertIn('american_odds', json.dumps(json.loads(detail.content[0].text)))
            self.assertIn('no_vig_market_probability', json.dumps(json.loads(detail.content[0].text)))
            self.assertNotIn(str(self.state), json.dumps(json.loads(detail.content[0].text)))
        self.assertEqual(len(self.calls), count)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(len(list((self.state / 'research-snapshots').glob('*.json'))), 1)

    async def test_invalid_arguments_and_sanitized_errors(self):
        async with Client(adapter.build_server(self.state)) as client:
            for name, args, expected in (
                ('research_summary', {'snapshot_id':'bad'}, 'INVALID_RESEARCH_ID'),
                ('research_summary', {'snapshot_id':'a'*32}, 'RESEARCH_EVIDENCE_UNAVAILABLE'),
                ('segment_comparison', {'snapshot_id':'a'*32,'segment':'team'}, 'UNSUPPORTED_RESEARCH_SEGMENT'),
                ('inspect_fixture', {'snapshot_id':'a'*32,'prediction_id':'invalid'}, 'INVALID_RESEARCH_ID'),
                ('create_research_snapshot', {'state_dir':'/secret/private'}, 'INVALID_RESEARCH_ARGUMENTS'),
                ('research_summary', {'snapshot_id':'a'*32,'command':'cat /secret'}, 'INVALID_RESEARCH_ARGUMENTS'),
                ('arbitrary_shell', {'command':'cat /secret'}, 'UNSUPPORTED_RESEARCH_TOOL'),
            ):
                result = await client.call_tool(name, args)
                self.assertTrue(result.is_error)
                self.assertEqual(result.content[0].text, expected)
                self.assertNotIn('/secret', str(result))
                self.assertNotIn('Traceback', str(result))
            with patch.object(domain, 'research_summary', side_effect=OSError('/private/credential')):
                result = await client.call_tool('research_summary', {'snapshot_id':'a'*32})
            self.assertEqual(result.content[0].text, 'RESEARCH_EVIDENCE_UNAVAILABLE')

    async def test_runner_lock_unavailable_and_no_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            async with Client(adapter.build_server(state)) as client:
                result = await client.call_tool('create_research_snapshot', {})
            self.assertEqual(result.content[0].text, 'RESEARCH_EVIDENCE_UNAVAILABLE')
            self.assertFalse(state.exists())

    async def test_domain_failure_and_existing_manifest_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            (state / 'prospective').mkdir(parents=True)
            (state / 'prospective' / 'runner.lock').touch()
            async with Client(adapter.build_server(state)) as client:
                with patch.object(domain, 'create_snapshot', side_effect=OSError('/secret/key')):
                    result = await client.call_tool('create_research_snapshot', {})
                self.assertTrue(result.is_error)
                self.assertEqual(result.content[0].text, 'RESEARCH_EVIDENCE_UNAVAILABLE')
                self.assertFalse((state / 'research-snapshots').exists())
                first = await client.call_tool('create_research_snapshot', {})
                self.assertFalse(first.is_error)
                original = next((state / 'research-snapshots').glob('*.json'))
                payload = original.read_bytes()
                with patch.object(domain, 'research_summary', side_effect=OSError('/secret/key')):
                    result = await client.call_tool('create_research_snapshot', {})
                self.assertTrue(result.is_error)
                self.assertEqual(result.content[0].text, 'RESEARCH_EVIDENCE_UNAVAILABLE')
                self.assertEqual(original.read_bytes(), payload)
                self.assertEqual(len(list((state / 'research-snapshots').glob('*.json'))), 2)

    async def test_concurrent_calls_fail_busy_without_queuing_domain_work(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocking(state, name, arguments, request_id):
            calls.append(name)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
            return adapter.CallToolResult(content=[adapter.TextContent(type='text', text='{}')])

        with patch.object(adapter, '_result', side_effect=blocking):
            async with Client(adapter.build_server(self.state)) as client:
                first = asyncio.create_task(client.call_tool('research_summary', {'snapshot_id': 'a'*32}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    busy = await client.call_tool('research_summary', {'snapshot_id': 'a'*32})
                    self.assertTrue(busy.is_error)
                    self.assertEqual(busy.content[0].text, 'RESEARCH_BUSY')
                    self.assertEqual(calls, ['research_summary'])
                finally:
                    release.set()
                self.assertFalse((await first).is_error)
                self.assertFalse((await client.call_tool('research_summary', {'snapshot_id': 'a'*32})).is_error)
        self.assertEqual(calls, ['research_summary', 'research_summary'])

    async def test_canceled_handler_stays_busy_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocking(state, name, arguments, request_id):
            calls.append(name)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
            return adapter.CallToolResult(content=[adapter.TextContent(type='text', text='{}')])

        with patch.object(adapter, '_result', side_effect=blocking):
            async with Client(adapter.build_server(self.state)) as client:
                first = asyncio.create_task(client.call_tool('research_summary', {'snapshot_id': 'a'*32}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    busy = await client.call_tool('research_summary', {'snapshot_id': 'a'*32})
                    self.assertTrue(busy.is_error)
                    self.assertEqual(busy.content[0].text, 'RESEARCH_BUSY')
                    self.assertEqual(calls, ['research_summary'])
                finally:
                    release.set()
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if (await client.call_tool('research_summary', {'snapshot_id': 'a'*32})).is_error is False:
                        break
                self.assertEqual(calls, ['research_summary', 'research_summary'])

    async def test_fixture_not_in_snapshot_and_transport_result_bound(self):
        self.run_pilot()
        async with Client(adapter.build_server(self.state)) as client:
            identity = json.loads((await client.call_tool('create_research_snapshot', {})).content[0].text)['snapshot_id']
            result = await client.call_tool('inspect_fixture', {'snapshot_id': identity, 'prediction_id': 'a'*32})
            self.assertTrue(result.is_error)
            self.assertEqual(result.content[0].text, 'PREDICTION_NOT_IN_SNAPSHOT')
            with patch.object(adapter, 'MAX_MCP_RESULT_BYTES', 2):
                result = await client.call_tool('research_summary', {'snapshot_id': identity})
            self.assertTrue(result.is_error)
            self.assertEqual(result.content[0].text, 'RESEARCH_OUTPUT_LIMIT')

    async def test_complete_envelope_bound_includes_json_escaping(self):
        output = {'sample': '"' * 2000}
        with patch.object(domain, 'research_summary', return_value=output):
            with patch.object(adapter, 'MAX_MCP_RESULT_BYTES', len(json.dumps(output)) + 300):
                result = adapter._result(Path('/unused'), 'research_summary', {'snapshot_id': 'a'*32}, 1)
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, 'RESEARCH_OUTPUT_LIMIT')

    async def test_bound_uses_actual_string_request_id(self):
        output = {'sample': 'x' * 1000}
        args = {'snapshot_id': 'a'*32}
        with patch.object(domain, 'research_summary', return_value=output):
            short = adapter._result(Path('/unused'), 'research_summary', args, 1)
            self.assertFalse(short.is_error)
            size = len(json.dumps({'jsonrpc': '2.0', 'id': 1,
                                  'result': short.model_dump(by_alias=True, exclude_none=True)},
                                  separators=(',', ':'), ensure_ascii=False).encode())
            with patch.object(adapter, 'MAX_MCP_RESULT_BYTES', size + 10):
                self.assertFalse(adapter._result(Path('/unused'), 'research_summary', args, 1).is_error)
                long = adapter._result(Path('/unused'), 'research_summary', args, 'x'*300)
            self.assertTrue(long.is_error)
            self.assertEqual(long.content[0].text, 'RESEARCH_OUTPUT_LIMIT')

    async def test_stdio_rejects_oversized_id_before_sdk_dispatch(self):
        environment = {'PATH': os.environ.get('PATH', ''),
                       'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
                       'MODELFC_STATE_DIR': '/unused'}
        request = json.dumps({'jsonrpc': '2.0', 'id': 'x' * (adapter.MAX_REQUEST_ID_BYTES + 1),
                              'method': 'tools/list', 'params': {}}) + '\n'
        self.assert_input_limit_terminates_with_open_pipe(request, environment)

    async def test_raw_stdin_line_limit_before_sdk_parse(self):
        environment = {'PATH': os.environ.get('PATH', ''),
                       'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
        request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list',
                              'params': {'padding': 'x' * adapter.MAX_INBOUND_MESSAGE_BYTES}}) + '\n'
        self.assert_input_limit_terminates_with_open_pipe(request, environment)

    def assert_input_limit_terminates_with_open_pipe(self, request, environment):
        child = subprocess.Popen([sys.executable, '-m', 'modelfc.private_research_mcp'],
                                 env=environment, text=True, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            child.stdin.write(request)
            child.stdin.flush()
            # Deliberately keep stdin open; the worker must terminate anyway.
            self.assertEqual(child.wait(timeout=5), 2)
            self.assertEqual(child.stdout.read(), '')
            self.assertEqual(child.stderr.read().strip(), 'RESEARCH_MCP_INPUT_LIMIT')
        finally:
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()
            if child.poll() is None:
                child.kill()
                child.wait()

    async def test_utf8_request_id_boundary_and_correlation(self):
        environment = {'PATH': os.environ.get('PATH', ''),
                       'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
        for identity in ('a' * adapter.MAX_REQUEST_ID_BYTES, 'é' * 100):
            with self.subTest(identity=identity[:10]):
                request = json.dumps({'jsonrpc': '2.0', 'id': identity,
                                      'method': 'tools/list', 'params': {}}) + '\n'
                completed = subprocess.run([sys.executable, '-m', 'modelfc.private_research_mcp'],
                                           input=request, env=environment, text=True,
                                           capture_output=True, timeout=10)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)['id'], identity)
        self.assertTrue(adapter._id_is_oversized('é' * 129))
        self.assertFalse(adapter._id_is_oversized('é' * 128))

    async def test_stdio_transport_modern_and_legacy_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            (state / 'prospective').mkdir(parents=True)
            (state / 'prospective' / 'runner.lock').touch()
            environment = {'PATH': os.environ.get('PATH',''), 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
                           'MODELFC_STATE_DIR': str(state)}
            params = StdioServerParameters(command=sys.executable,
                                           args=['-m', 'modelfc.private_research_mcp'], env=environment)
            for mode in ('auto', 'legacy'):
                async with Client(params, mode=mode) as client:
                    self.assertEqual(len((await client.list_tools()).tools), 4)
                    result = await client.call_tool('create_research_snapshot', {})
                    self.assertFalse(result.is_error)
                    self.assertEqual(json.loads(result.content[0].text)['coverage']['predictions_included'], 0)
            self.assertEqual(len(list((state / 'research-snapshots').glob('*.json'))), 2)


class PrivateResearchHostTemplateTests(unittest.TestCase):
    def test_physical_release_matches_bound_inode_and_marker_sha(self):
        path = Path(__file__).resolve().parents[1] / 'ops/vps/research_mcp_launch.py'
        spec = importlib.util.spec_from_file_location('research_mcp_launch_physical', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a'*40
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            releases = base / 'releases'
            candidate = releases / (sha + '-' + 'b'*12)
            candidate.mkdir(parents=True)
            other = base / 'other'
            other.mkdir()
            with patch.object(module, 'RELEASES', releases):
                self.assertEqual(module._physical_release(candidate, sha, os.geteuid()), candidate)
                with self.assertRaises(ValueError):
                    module._physical_release(other, sha, os.geteuid())
                with self.assertRaises(ValueError):
                    module._physical_release(candidate, 'c'*40, os.geteuid())

                source_file = candidate / 'src/modelfc/ledger_storage.py'
                source_file.parent.mkdir(parents=True)
                source_file.touch()
                with patch.object(ledger_storage, '__file__', str(source_file)), \
                        patch.object(ledger_storage, '_deployed_commit_sha', return_value=sha) as pinned:
                    self.assertEqual(ledger_storage.git_commit_sha(), sha)
                    pinned.assert_called_once_with(candidate)

    def test_bind_mount_detection_handles_same_device_and_requires_readonly(self):
        path = Path(__file__).resolve().parents[1] / 'ops/vps/research_mcp_launch.py'
        spec = importlib.util.spec_from_file_location('research_mcp_launch_mount', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The bind source and parent share st_dev. mountinfo still names the mount.
        line = '31 20 8:1 /modelfc/releases/abc /srv/modelfc-research-release ro,relatime - ext4 /dev/vda1 rw\n'
        with patch('builtins.open', mock_open(read_data=line)):
            self.assertTrue(module._pinned_readonly_mount())
        with patch('builtins.open', mock_open(read_data=line.replace(' ro,', ' rw,'))):
            self.assertFalse(module._pinned_readonly_mount())
        with patch('builtins.open', mock_open(read_data=line.replace('/srv/modelfc-research-release', '/other'))):
            self.assertFalse(module._pinned_readonly_mount())

    def test_private_unit_and_no_public_ingress(self):
        root = Path(__file__).resolve().parents[1]
        unit = (root / 'ops/vps/modelfc-research-mcp.service').read_text()
        self.assertIn('User=modelfc-tunnel', unit)
        self.assertIn('ProtectSystem=strict', unit)
        self.assertIn('ReadWritePaths=/var/lib/modelfc/state/research-snapshots ', unit)
        self.assertIn('InaccessiblePaths=/etc/modelfc/credentials', unit)
        self.assertNotIn('modelfc-api', unit)
        self.assertNotIn('ODDSPAPI', unit)
        self.assertIn('LoadCredential=tunnel.key:', unit)
        self.assertIn('BindReadOnlyPaths=/srv/modelfc/current:/srv/modelfc-research-release', unit)
        self.assertIn('RestrictNamespaces=yes', unit)
        sudoers = (root / 'ops/vps/modelfc-research-mcp.sudoers').read_text()
        self.assertEqual(len([line for line in sudoers.splitlines() if line and not line.startswith('#')]), 1)
        self.assertIn('modelfc-tunnel ALL=(modelfc-runtime) NOPASSWD:', sudoers)
        for path in (root / 'ops/vps').glob('*Caddy*'):
            self.assertNotIn('/mcp', path.read_text())

    def test_mcp_child_environment_strips_tunnel_and_provider_keys(self):
        path = Path(__file__).resolve().parents[1] / 'ops/vps/research_mcp_launch.py'
        spec = importlib.util.spec_from_file_location('research_mcp_launch', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertNotIn('Path.cwd()', path.read_text())
        physical = Path('/srv/modelfc/releases/' + 'a'*40 + '-bbbbbbbbbbbb')
        with patch.object(module, '_pinned_readonly_mount', return_value=True), \
                patch.object(module, '_physical_release', return_value=physical), \
                patch.object(module.pwd, 'getpwnam') as account, \
                patch.object(module.os, 'geteuid', return_value=1000), \
                patch.object(module, '_protected'), \
                patch.object(module.os, 'open', return_value=0), \
                patch.object(module.os, 'fdopen') as source, \
                patch.object(module.os, 'access', return_value=True), \
                patch.object(module.os, 'execve', side_effect=RuntimeError('stop')) as execute, \
                patch.dict(os.environ, {'CONTROL_PLANE_API_KEY': 'private',
                                        'ODDSPAPI_API_KEY': 'provider'}):
            account.return_value.pw_uid = 1000
            handle = source.return_value.__enter__.return_value
            handle.fileno.return_value = 0
            handle.read.return_value = b'a'*40
            with patch.object(module.os, 'fstat') as status, \
                    patch.object(module.Path, 'is_file', return_value=True), \
                    self.assertRaises(RuntimeError):
                status.return_value.st_mode = 0o100444
                status.return_value.st_uid = 1000
                status.return_value.st_size = 40
                module.launch()
            environment = execute.call_args.args[2]
            self.assertEqual(environment['MODELFC_STATE_DIR'], '/var/lib/modelfc/state')
            self.assertEqual(environment['PYTHONPATH'], str(physical / 'src'))
            self.assertNotIn('CONTROL_PLANE_API_KEY', environment)
            self.assertNotIn('ODDSPAPI_API_KEY', environment)
