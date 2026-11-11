"""Optional real-SDK compatibility gate, using synthetic bytes and no network.

Ordinary discovery skips if the pinned SDK is absent. Operator acceptance must
instead run ``python -B -m tests.test_agents_sdk_release --check-installed``:
that mode fails when the exact SDK is absent and never treats a skip as a pass.
This module installs nothing and never imports a downloaded source tree.
"""
import importlib.metadata
import json
import os
import socket
import sys
import unittest
from unittest.mock import patch

import httpx

from ops.bounded_worker.agents_live_pilot import LIMIT, SDK_VERSION, SDKEvents, LiveFailure
from ops.bounded_worker.agents_repository_events import TaskSDKEvents


# Exact released union and member schema, not a production event or receipt:
# https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/types/beta/agent_session_event.py
# https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/types/beta/agent_session_turn_output_text_delta_event.py
SYNTHETIC_EVENT = {
    'type': 'agent.session.turn.output_text.delta', 'event_id': 'ev_synthetic',
    'session_id': 'sess_synthetic', 'turn_id': 'turn_synthetic',
    'item_id': 'msg_synthetic', 'output_index': 0, 'content_index': 0,
    'delta': 'synthetic text',
}


def installed_release():
    try:
        return importlib.metadata.version('openai') == SDK_VERSION
    except importlib.metadata.PackageNotFoundError:
        return False


@unittest.skipUnless(installed_release(), 'exact openai==3.47.0 not installed; not SDK acceptance')
class InstalledSDKReleaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.stream_closed = self.transport_closed = 0
        frame = ('event: ' + SYNTHETIC_EVENT['type'] + '\ndata: '
                 + json.dumps(SYNTHETIC_EVENT) + '\n\n').encode('ascii')
        self.chunks = [frame[:23], frame[23:], b'data: [DONE]\n\n']
        owner = self

        class SyntheticStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for chunk in owner.chunks:
                    yield chunk

            async def aclose(self):
                owner.stream_closed += 1

        class SyntheticTransport(httpx.AsyncBaseTransport):
            def __init__(self, **kwargs):
                owner.transport_options = kwargs

            async def handle_async_request(self, request):
                owner.requests.append(request)
                return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                    stream=SyntheticStream())

            async def aclose(self):
                owner.transport_closed += 1

        # The genuine SDK builds the request and parses SSE. Only the lowest
        # transport is replaced. Accidental socket or subprocess use must fail.
        for guard in (patch.dict(os.environ, {}, clear=True),
                patch.object(httpx, 'AsyncHTTPTransport', SyntheticTransport),
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('no network')),
                patch.object(socket.socket, 'connect', side_effect=AssertionError('no network')),
                patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('no network')),
                patch('subprocess.Popen', side_effect=AssertionError('no process'))):
            guard.start()
            self.addCleanup(guard.stop)

    async def test_actual_release_request_and_lifecycle_for_both_wrappers(self):
        from openai.types.beta.agent_session_turn_output_text_delta_event import (
            AgentSessionTurnOutputTextDeltaEvent,
        )
        for wrapper in (SDKEvents, lambda key: TaskSDKEvents(key, 1024)):
            with self.subTest(wrapper=wrapper):
                self.requests.clear()
                self.stream_closed = self.transport_closed = 0
                events = wrapper('synthetic-application-key')
                try:
                    iterator = await events.open('sess_synthetic')
                    parsed = [item async for item in iterator]
                    self.assertEqual(len(parsed), 1)
                    self.assertIsInstance(parsed[0], AgentSessionTurnOutputTextDeltaEvent)
                    self.assertEqual(parsed[0].model_dump(mode='json'), SYNTHETIC_EVENT)
                    self.assertEqual(len(self.requests), 1)
                    request = self.requests[0]
                    self.assertEqual(request.method, 'GET')
                    self.assertEqual(str(request.url),
                        'https://api.openai.com/v1/agents/sessions/sess_synthetic/events'
                        '?include_obfuscation=false')
                    self.assertEqual(request.headers['Accept-Encoding'], 'identity')
                    self.assertEqual(request.headers['Accept'], 'text/event-stream')
                    self.assertEqual(request.headers['OpenAI-Beta'], 'agents=v1')
                    self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-application-key')
                    self.assertNotIn('OpenAI-Organization', request.headers)
                    self.assertNotIn('OpenAI-Project', request.headers)
                    self.assertEqual(events.client.max_retries, 0)
                    self.assertEqual(events.client.timeout, 10)
                    self.assertEqual(self.transport_options, {'retries': 0, 'trust_env': False})
                finally:
                    await events.close()
                closed = self.stream_closed, self.transport_closed
                await events.close()
                self.assertEqual((self.stream_closed, self.transport_closed), closed)
                self.assertGreaterEqual(self.stream_closed, 1)
                self.assertEqual(self.transport_closed, 1)

    async def test_actual_release_caps_raw_bytes_before_sse_for_both_wrappers(self):
        for wrapper, cap in ((SDKEvents, LIMIT), (lambda key: TaskSDKEvents(key, 1024), 1024)):
            with self.subTest(cap=cap):
                self.requests.clear()
                self.chunks = [b'x' * cap, b'y']  # No SSE boundary to hide buffering.
                events = wrapper('synthetic-application-key')
                try:
                    with self.assertRaisesRegex(LiveFailure, 'stream_byte_limit'):
                        iterator = await events.open('sess_synthetic')
                        async for _ in iterator:
                            self.fail('unframed data reached the event consumer')
                    self.assertEqual(events.total, cap + 1)
                    self.assertEqual(len(self.requests), 1)
                finally:
                    await events.close()


if __name__ == '__main__':
    strict = '--check-installed' in sys.argv
    if strict:
        sys.argv.remove('--check-installed')
        if not installed_release():
            raise SystemExit('FAIL: exact openai==3.47.0 is required; no installed-SDK acceptance')
    result = unittest.main(exit=False).result
    accepted = result.wasSuccessful() and (not strict or (not result.skipped and result.testsRun == 2))
    raise SystemExit(0 if accepted else 1)
