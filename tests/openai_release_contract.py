"""Synthetic signatures, not an SDK implementation or installed-SDK evidence.

Bound call signatures transcribed from official v3.47.0 release commit
561ef6b4753f961782f0afc795276e91d5ea8d1d. Only annotations and bound ``self``
are omitted. SDK sentinel/constant defaults are opaque local placeholders;
these functions are autospec inputs only and never construct real clients.

https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/_client.py#L812-L832
https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/resources/beta/agents/sessions/events.py#L210-L220
https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/_streaming.py#L165-L168
https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/_streaming.py#L251-L257
https://github.com/openai/openai-python/blob/561ef6b4753f961782f0afc795276e91d5ea8d1d/src/openai/_base_client.py#L1679-L1681
"""

NOT_GIVEN = object()
DEFAULT_MAX_RETRIES = object()


def client_constructor(*, api_key=None, admin_api_key=None, workload_identity=None,
        organization=None, project=None, webhook_secret=None, provider=None,
        base_url=NOT_GIVEN, data_residency=None, websocket_base_url=None,
        timeout=NOT_GIVEN, max_retries=DEFAULT_MAX_RETRIES, default_headers=None,
        default_query=None, http_client=None, _strict_response_validation=False,
        _enforce_credentials=True):
    pass


async def events_stream(session_id, *, extra_headers=None, extra_query=None,
        extra_body=None, timeout=NOT_GIVEN):
    pass


async def stream_iterator():
    yield None


async def stream_close():
    pass


async def client_close():
    pass
