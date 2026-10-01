"""In-process and stdio MCP contract over synthetic, offline research evidence."""

import json
from importlib.util import find_spec
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

from modelfc import corner_research as domain
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
                result = adapter._result(Path('/unused'), 'research_summary', {'snapshot_id': 'a'*32})
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, 'RESEARCH_OUTPUT_LIMIT')

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
        release = Path('/srv/modelfc/releases/' + 'a'*40 + '-aaaaaaaaaaaa')
        with patch.object(module.Path, 'cwd', return_value=release), \
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
            self.assertNotIn('CONTROL_PLANE_API_KEY', environment)
            self.assertNotIn('ODDSPAPI_API_KEY', environment)
