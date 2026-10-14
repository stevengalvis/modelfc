"""Disabled cadence proposals from synthetic retained evidence, never downloads."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import os
import unittest
from unittest.mock import patch

from modelfc import refresh_cadence as cadence, odds_calendar, research_outcomes
from tests import test_automatic_research_settlement as fixtures
from tests.test_history_completeness import bundle, raw


class CadenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutomaticSettlementTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.payload += b'E1,20/09/2026,15:00,Birmingham,Blackburn,2,1,H,5,4\n'
        self.fixture.refresh()
        self.state = self.fixture.state
        self.now = datetime(2026,10,13,6,tzinfo=timezone.utc)

    def calendar(self, *, status='FINISHED', offset=timedelta(), kickoff=None):
        value = bundle(); value['retrieved_at'] = (self.now+offset).isoformat()
        payload = json.loads(value['payload'])
        for row in payload['matches']:
            if row['utcDate'] == '2026-10-09T12:00:00Z':
                row['status'] = status
                if kickoff: row['utcDate'] = kickoff
                row['score']['fullTime'] = {'home':2,'away':1} if status=='FINISHED' else {'home':None,'away':None}
        payload['resultSet']['played'] = sum(r['status']=='FINISHED' for r in payload['matches'])
        value['payload'] = json.dumps(payload)
        odds_calendar.publish(self.state, raw(value), as_of=self.now+offset)

    def plan(self, now=None):
        before = {p:p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            result = cadence.plan(self.state, policy=cadence.POLICY, as_of=now or self.now, config=self.fixture.config)
        self.assertEqual(before, {p:p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        self.assertEqual(result['provider_requests'],0)
        self.assertEqual(result['status'],'DISABLED')
        return result

    def test_morning_only_never_activates_or_requires_provider(self):
        self.assertFalse(self.plan(self.now.replace(hour=6))['would_request'])  # Missing calendar is unknown.
        self.assertFalse(self.plan(self.now.replace(hour=18))['would_request'])
        self.assertFalse(self.plan(self.now.replace(hour=17))['would_request'])
        for policy in ({**cadence.POLICY,'enabled':True}, {**cadence.POLICY,'competition':'E0'}, {**cadence.POLICY,'version':True}):
            self.assertEqual(cadence.plan(self.state,policy=policy,as_of=self.now,config=self.fixture.config)['status'],'REJECTED')
        self.assertIn('OnCalendar=Mon,Thu *-*-* 06:00:00 UTC',Path('deploy/modelfc-corner-refresh.timer').read_text())
        self.assertEqual(json.loads(Path('deploy/proposals/refresh-cadence.json').read_text()),cadence.POLICY)
        budget=cadence.plan(self.state,policy=cadence.POLICY,as_of=self.now.replace(hour=6),config=self.fixture.config)
        self.assertEqual(budget['max_download_attempts_per_opportunity'],0)
        self.assertEqual(budget['max_http_retries_per_league'],0)
        self.assertEqual(budget['conditional_http_cache'],'NOT_ENABLED')
        self.assertEqual(budget['provider_requests'],0)

    def test_finished_pending_and_settled_markets_independent(self):
        self.calendar()
        result=self.plan()
        self.assertFalse(result['would_request'],result)  # Already published: offline settlement only.
        self.assertEqual({r['market'] for r in result['pending']},{'BTTS','CORNER'})
        self.assertEqual(len({r['fixture_id'] for r in result['pending']}),1)
        # A first publication may lack corners; normal refresh forbids removing
        # previously published counts, so start a fresh synthetic publication.
        (self.fixture.history/'E1_2627.csv').unlink()
        self.fixture.refresh(self.fixture.payload.replace(b',6,3',b',,'))
        self.fixture.run_settlement()
        # Settlement records were written at real current time; read after them.
        after=datetime.now(timezone.utc).replace(hour=6,minute=0,second=0,microsecond=0)+timedelta(days=1)
        # Test immutable outcomes at a later evaluation time without changing them.
        self.now=after; self.calendar()
        self.assertEqual([r['market'] for r in self.plan()['pending']],['CORNER'])
        self.fixture.refresh(); self.fixture.run_settlement()
        self.assertFalse(self.plan()['would_request'])

    def test_missing_stale_nonfinal_or_mismatched_calendar_does_not_qualify(self):
        self.assertFalse(self.plan()['would_request'])
        self.calendar(status='TIMED')
        self.assertFalse(self.plan()['would_request'])
        self.calendar(kickoff='2026-10-09T12:01:00Z',offset=timedelta(seconds=1))
        self.assertFalse(self.plan(self.now+timedelta(seconds=1))['would_request'])
        self.assertFalse(self.plan(self.now+timedelta(days=3))['would_request'])

    def test_actual_plan_command_stays_disabled_and_offline(self):
        guard=self.fixture.fixture.root/'cadence-guard';guard.mkdir()
        (guard/'sitecustomize.py').write_text("import socket\nsocket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))\n")
        result=subprocess.run([sys.executable,'-m','modelfc.refresh_cadence','--state-dir',str(self.state),'--config',str(self.fixture.config_file),'--policy','deploy/proposals/refresh-cadence.json','--as-of','2026-10-13T06:00:00Z'],env={**os.environ,'PYTHONPATH':os.pathsep.join((str(guard),str(Path('src').resolve())))},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'DISABLED')
        self.assertEqual(json.loads(result.stdout)['provider_requests'],0)

    def test_future_or_corrupt_saved_calendar_never_qualifies(self):
        self.calendar(offset=timedelta(minutes=1))
        self.assertFalse(self.plan()['would_request'])
        pin=json.loads((self.state/'odds-calendar/latest.json').read_text())['bundle_sha256']
        path=self.state/'odds-calendar'/f'{pin}.json'
        path.write_bytes(path.read_bytes()+b'corrupt')
        result=self.plan(self.now+timedelta(seconds=1))
        self.assertFalse(result['would_request'])
        self.assertEqual(result['reason'],'SAVED_EVIDENCE_UNAVAILABLE_OR_INVALID')

    def test_missing_training_without_forecasts_qualifies_one_e1_request_only(self):
        from modelfc.corner_data import CornerDataConfig
        self.calendar()
        empty=self.fixture.fixture.root/'no-forecasts';empty.mkdir(mode=0o700)
        (empty/'prospective').mkdir();(empty/'prospective/runner.lock').touch()
        odds_calendar.publish(empty,odds_calendar.retained(self.state),as_of=self.now)
        target=self.fixture.history/'E1_2627.csv'
        target.write_bytes(self.fixture.payload.replace(self.fixture.fixture.content.splitlines()[1].encode()+b'\n',b''))
        config=CornerDataConfig(self.fixture.history,('E1','SP1'),14)
        result=cadence.plan(empty,policy=cadence.POLICY,as_of=self.now,config=config)
        self.assertTrue(result['would_request'],result)
        self.assertEqual(result['pending'],[])
        self.assertEqual(len(result['missing_training_fixture_ids']),1)
        self.assertEqual(result['eligible_leagues'],['E1'])
        self.assertEqual(result['unassessed_leagues'],['SP1'])
        self.assertEqual(result['max_download_attempts_per_opportunity'],1)
        self.assertFalse((empty/'.lock').exists())
        target.unlink()  # No current season CSV also needs history, without a forecast.
        self.assertEqual(len(cadence.plan(empty,policy=cadence.POLICY,as_of=self.now,config=config)['missing_training_fixture_ids']),2)
