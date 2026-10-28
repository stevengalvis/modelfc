"""Canned CLI events only; no CLI, account, model or process execution."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

P = Path(__file__).resolve().parents[1] / 'docs/zen53/model-patch/capture.py'
spec = importlib.util.spec_from_file_location('model_capture', P)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def events():
    return [{'type':'thread.started','thread_id':'synthetic-thread'},
            {'type':'turn.started'},
            {'type':'item.completed','item':{'id':'item_0','type':'agent_message','text':'{"synthetic":true}'}},
            {'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':4}}]


def encode(rows):
    return b''.join(json.dumps(row).encode()+b'\n' for row in rows)


class ModelCapture(unittest.TestCase):
    def test_single_turn_and_optional_reasoning(self):
        rows=events()
        self.assertEqual(c.extract(encode(rows),0,False),b'{"synthetic":true}')
        rows.insert(2,{'type':'item.completed','item':{'id':'reason_0','type':'reasoning','text':'synthetic'}})
        self.assertEqual(c.extract(encode(rows),0,False),b'{"synthetic":true}')
        self.assertEqual(c.receipt(encode(rows),0,False)['state'],'OBSERVED_NO_TOOL_SINGLE_TURN')

    def test_every_nonmessage_tool_error_and_extra_turn_rejected(self):
        for kind in ('command_execution','file_change','mcp_tool_call','collab_tool_call','web_search','todo_list','error','unknown'):
            rows=events();rows[2]['item']['type']=kind
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'^MODEL_CAPTURE_REJECTED$'):
                c.extract(encode(rows),0,False)
        rows=events();rows.insert(2,{'type':'turn.started'})
        with self.assertRaises(ValueError):c.extract(encode(rows),0,False)

    def test_schema_duplicates_bounds_status_and_no_partial_acceptance(self):
        raw=encode(events())
        bad=[raw[:-1],b'x'*65537,raw.replace(b'"type": "turn.started"',b'"type":"turn.started","type":"turn.started"'),raw+b'{}\n']
        for value in bad:
            with self.assertRaises(ValueError):c.extract(value,0,False)
        for code,timeout in ((1,False),(0,True),(False,False),(0,0)):
            with self.assertRaises(ValueError):c.extract(raw,code,timeout)
        for mutate in ('unknown','duplicate_item','bool_usage','long_text','wrong_order'):
            rows=events()
            if mutate=='unknown':rows[2]['item']['extra']='private'
            elif mutate=='duplicate_item':rows.insert(2,copy.deepcopy(rows[2]))
            elif mutate=='bool_usage':rows[-1]['usage']['output_tokens']=True
            elif mutate=='long_text':rows[2]['item']['text']='x'*4097
            else:rows[1],rows[2]=rows[2],rows[1]
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):c.extract(encode(rows),0,False)
