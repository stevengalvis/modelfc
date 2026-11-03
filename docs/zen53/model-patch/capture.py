"""Pure bounded Codex 0.164 JSONL inspection, not authentication or tool prevention."""
import hashlib
import json
import re


def need(ok):
    if not ok:
        raise ValueError('MODEL_CAPTURE_REJECTED')


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result)
        result[key] = value
    return result


def extract(raw, returncode, timedout):
    """Return final response bytes; caller authenticates provenance and execution status.

    The accepted subset is one turn, completed reasoning items and one final
    agent message. Unknown events are rejected, never interpreted as harmless.
    This observes reported tool activity; it cannot prevent host-side tools.
    """
    try:
        need(type(returncode) is int and returncode == 0 and timedout is False)
        need(type(raw) is bytes and 0 < len(raw) <= 65536 and raw.endswith(b'\n'))
        lines = raw.splitlines()
        need(4 <= len(lines) <= 16 and all(lines))
        events = [json.loads(line, object_pairs_hook=pairs) for line in lines]
        need(all(type(event) is dict for event in events))
        first = events[0]
        need(set(first) == {'type', 'thread_id'} and first['type'] == 'thread.started')
        need(type(first['thread_id']) is str and re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', first['thread_id']))
        need(events[1] == {'type': 'turn.started'})
        last = events[-1]
        need(set(last) == {'type', 'usage'} and last['type'] == 'turn.completed')
        usage = last['usage']
        required = {'input_tokens', 'cached_input_tokens', 'output_tokens'}
        need(type(usage) is dict and required <= set(usage) <= required | {'cache_write_input_tokens', 'reasoning_output_tokens'})
        need(all(type(v) is int and 0 <= v < 2**63 for v in usage.values()))
        ids, messages = set(), []
        for index, event in enumerate(events[2:-1]):
            need(set(event) == {'type', 'item'} and event['type'] == 'item.completed')
            item = event['item']
            need(type(item) is dict and set(item) == {'id', 'type', 'text'})
            need(type(item['id']) is str and re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', item['id']) and item['id'] not in ids)
            ids.add(item['id'])
            need(item['type'] in ('reasoning', 'agent_message') and type(item['text']) is str)
            need(len(item['text'].encode('utf-8')) <= 8192)
            if item['type'] == 'agent_message':
                need(index == len(events) - 4)
                messages.append(item['text'].encode('utf-8'))
        need(len(messages) == 1 and len(messages[0]) <= 4096)
        return messages[0]
    except Exception:
        raise ValueError('MODEL_CAPTURE_REJECTED') from None


def receipt(raw, returncode, timedout):
    response = extract(raw, returncode, timedout)
    return {'state': 'OBSERVED_NO_TOOL_SINGLE_TURN',
            'capture_sha256': hashlib.sha256(raw).hexdigest(),
            'response_sha256': hashlib.sha256(response).hexdigest()}
