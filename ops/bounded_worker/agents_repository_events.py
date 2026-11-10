"""Task-sized pre-parser API event bound, separate from inert source export.

Uses the same pinned SDK and exact official event endpoint as PR267, whose
existing SDKEvents interface remains unchanged. No requests occur on import.
"""
import importlib.metadata
import os
import re
from .agents_live_pilot import LIMIT, SDK_VERSION, LiveFailure, _key


class TaskSDKEvents:
    """Official SDK SSE, with a bounded legacy-httpx transport below its parser.

The pinned SDK explicitly supports an injected legacy httpx.AsyncClient. No SDK
or HTTP dependency is installed here. Ambient custom headers are rejected.
"""
    def __init__(self, application_key, max_bytes):
        if type(max_bytes) is not int or not 1024 <= max_bytes <= LIMIT:
            raise ValueError('task_stream_limit')
        _key(application_key)
        if 'OPENAI_CUSTOM_HEADERS' in os.environ:
            raise LiveFailure('ambient_sdk_headers_rejected')
        if importlib.metadata.version('openai') != SDK_VERSION:
            raise LiveFailure('sdk_version_unverified')
        import httpx
        from openai import AsyncOpenAI

        owner = self
        self.session_id = None
        self.stream = None
        self.total = 0
        self.closed = False

        class LimitedStream(httpx.AsyncByteStream):
            def __init__(self, inner):
                self.inner = inner

            async def __aiter__(self):
                async for chunk in self.inner:
                    owner.total += len(chunk)
                    if owner.total > max_bytes:
                        raise LiveFailure('stream_byte_limit')
                    yield chunk

            async def aclose(self):
                await self.inner.aclose()

        class Transport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

            async def handle_async_request(self, request):
                expected = '/v1/agents/sessions/' + str(owner.session_id) + '/events'
                if (request.method != 'GET' or request.url.scheme != 'https'
                        or request.url.host != 'api.openai.com'
                        or request.url.port not in (None, 443) or request.url.path != expected):
                    raise LiveFailure('stream_target_rejected')
                response = await self.inner.handle_async_request(request)
                if response.headers.get('content-encoding', 'identity') != 'identity':
                    await response.aclose()
                    raise LiveFailure('encoded_stream_rejected')
                response.stream = LimitedStream(response.stream)
                return response

            async def aclose(self):
                await self.inner.aclose()

        timeout = httpx.Timeout(10, connect=5, pool=5)
        http_client = httpx.AsyncClient(transport=Transport(), trust_env=False,
                                       follow_redirects=False, timeout=timeout)
        self.client = AsyncOpenAI(api_key=application_key, admin_api_key='', webhook_secret='',
            organization='', project='', provider=None, base_url='https://api.openai.com/v1',
            max_retries=0, timeout=10, http_client=http_client)
        self.client.organization = None
        self.client.project = None

    async def open(self, session_id):
        if self.session_id is not None or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
            raise LiveFailure('stream_not_admitted')
        self.session_id = session_id
        self.stream = await self.client.beta.agents.sessions.events.stream(
            session_id, include_obfuscation=False, extra_headers={'Accept-Encoding': 'identity'})
        return self.stream.__aiter__()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.stream is not None:
                await self.stream.close()
        finally:
            await self.client.close()

