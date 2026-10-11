"""Offline saved-result settlement, synthetic provider IDs and full calendar."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc import corner_analysis_outcomes as outcomes
from modelfc.corner_analysis_store import _canonical_hash
from modelfc.sportmonks_outcome_evidence import validate_saved_result, read_pinned
from tests import test_corner_analysis_outcomes as outcome_tests
from tests.test_history_completeness import bundle
from tests.test_sportmonks_results import fixture
from tests import test_corner_prospective_api as api_tests


class SavedEvidence:
    def __init__(self):
        value = bundle()
        self.bindings = [{"provider_team_id": row["source_team_id"],
            "provider_name": row["source_name"], "history_name": row["history_name"]}
            for row in value["team_bindings"]]
        names = {row['history_name']: row for row in self.bindings}
        self.row = fixture()
        self.row['starting_at'] = '2026-09-20T11:00:00Z'
        self.row['starting_at_timestamp'] = int(datetime(2026,9,20,11,tzinfo=timezone.utc).timestamp())
        home, away = names['Wolves'], names['West Brom']
        self.row['participants'] = [{"id": b['provider_team_id'], "name": b['provider_name'],
            "meta": {"location": side}} for b, side in ((home,'home'),(away,'away'))]
        self.row['scores'] = [{"description":"CURRENT","participant_id":b['provider_team_id'],
            "score":{"goals":goals}} for b,goals in ((home,2),(away,1))]
        self.row['statistics'] = [{"type_id":34,"participant_id":b['provider_team_id'],
            "location":side,"data":{"value":corners}} for b,side,corners in
            ((home,'home',6),(away,'away',3))]
        payload = json.loads(value['payload'])
        match = next(r for r in payload['matches'] if r['homeTeam']['id'] == home['provider_team_id']
                     and r['awayTeam']['id'] == away['provider_team_id'])
        match.update(utcDate=self.row['starting_at'], status='FINISHED',
                     score={'fullTime':{'home':2,'away':1}})
        payload['resultSet']['played'] = 2
        value['payload'] = json.dumps(payload)
        self.calendar = json.dumps(value).encode()

    def evidence(self, **changes):
        response = json.dumps({'data':[self.row], 'pagination':{'has_more':False,'next_page':None}}).encode()
        args = dict(response=response,response_sha256=hashlib.sha256(response).hexdigest(),
            calendar_raw=self.calendar,calendar_sha256=hashlib.sha256(self.calendar).hexdigest(),
            capture_reference='saved-capture-001',retrieved_at=datetime(2026,10,9,22,tzinfo=timezone.utc),
            bindings=self.bindings,season_id=27903,fixture_id=self.row['id'])
        args.update(changes)
        return validate_saved_result(**args)


class SportmonksOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.saved = SavedEvidence()
        self.setup = outcome_tests.OutcomeTests(); self.setup.setUp()
        self.addCleanup(self.setup.doCleanups)

    def record(self, evidence=None):
        return outcomes.record_sportmonks_outcome(state_dir=self.setup.state,
            analysis_id=self.setup.analysis_id,evidence=evidence or self.saved.evidence(),
            idempotency_key='saved-final-1')

    def test_saved_final_idempotent_provenance_and_original_capture(self):
        record,created=self.record(); self.assertTrue(created)
        path=self.setup.outdir / (record['outcome_id']+'.json'); original=path.read_bytes()
        again,created=self.record(); self.assertFalse(created); self.assertEqual(record,again)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.setup.capture_path.read_bytes(),self.setup.original)
        source=record['source']; self.assertEqual(source['provider'],'sportmonks')
        self.assertEqual(source['normalized_record']['provider_fixture_id'],19729059)
        self.assertEqual(source['normalized_record']['home_goals'],2)
        self.assertEqual(source['final_state'],'FT')
        self.assertEqual(outcomes.load_outcome(self.setup.state,self.setup.analysis_id,record['outcome_id']),record)

    def test_immutable_outcome_and_performance_reads_do_not_consult_live_clock(self):
        api=api_tests.ProspectiveApiTests();api.setUp();self.addCleanup(api.doCleanups)
        record,_=outcomes.record_sportmonks_outcome(state_dir=api.state,analysis_id=api.analysis_id,
            evidence=self.saved.evidence(),idempotency_key='clock-independent')
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): raise AssertionError('immutable reads must not use live clock')
        with patch('modelfc.sportmonks_outcome_evidence.datetime',Clock):
            self.assertEqual(outcomes.load_outcome(api.state,api.analysis_id,record['outcome_id']),record)
            response=api.client.get('/api/v1/prospective/performance')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['model_performance']['settled_prediction_runs'],1)

    def test_initial_settlement_still_rejects_future_retrieval_under_corrected_clock(self):
        evidence=self.saved.evidence()
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return datetime(2026,9,23,tzinfo=timezone.utc)
        with patch('modelfc.sportmonks_outcome_evidence.datetime',Clock):
            with self.assertRaisesRegex(ValueError,'SAVED_RESULT_IDENTITY_CONFLICT'):self.record(evidence)
        self.assertFalse(self.setup.outdir.exists())

    def test_wrong_fixture_and_provenance(self):
        for changes in ({'fixture_id':1},{'season_id':1},{'response_sha256':'0'*64},
                        {'calendar_sha256':'0'*64},{'capture_reference':'/private/token'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.saved.evidence(**changes)
        self.assertFalse(self.setup.outdir.exists())

    def test_wrong_teams_kickoff_score_nonfinal_and_invalid_corners(self):
        base=deepcopy(self.saved.row)
        mutations=[lambda r:r['participants'][0].update(name='Unknown'),
            lambda r:r.update(starting_at='2026-09-21T15:00:00Z',
                              starting_at_timestamp=base['starting_at_timestamp']+86400),
            lambda r:r['scores'][0]['score'].update(goals=1),
            lambda r:r.update(state={'short_name':'NS'}),
            lambda r:r.update(statistics=r['statistics'][:1]),
            lambda r:r['statistics'].append(deepcopy(r['statistics'][0]))]
        mutations += [lambda r,v=v:r['statistics'][0]['data'].update(value=v) for v in (None,-1,1.5,True)]
        for mutate in mutations:
            self.saved.row=deepcopy(base); mutate(self.saved.row)
            with self.subTest(row=self.saved.row),self.assertRaises(ValueError): self.saved.evidence()
        self.assertFalse(self.setup.outdir.exists())

    def test_different_valid_saved_fixture_cannot_settle_capture(self):
        evidence=self.saved.evidence()
        # Existing capture checks also require the normalized fixture to match provider identity.
        self.setup.mutate_fixture(lambda r:r['response']['fixture'].update(away_team='QPR'))
        with self.assertRaises(ValueError): self.record(evidence)
        self.assertFalse(self.setup.outdir.exists())

    def test_conflicting_outcomes_never_overwrite(self):
        record,_=self.record(); before=(self.setup.outdir/(record['outcome_id']+'.json')).read_bytes()
        self.saved.row['statistics'][0]['data']['value']=7
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):self.record()
        self.assertEqual((self.setup.outdir/(record['outcome_id']+'.json')).read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'RESULT_CONFLICT'):self.setup.record()

    def test_corrupt_normalized_provenance_rejected_even_with_new_envelope_hash(self):
        record,_=self.record(); path=self.setup.outdir/(record['outcome_id']+'.json')
        record['source']['normalized_record']['home_corners']=10
        record['record_hash']=_canonical_hash({k:v for k,v in record.items() if k!='record_hash'})
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):outcomes.load_outcome(self.setup.state,self.setup.analysis_id,record['outcome_id'])

    def test_csv_compatibility_remains(self):
        record,_=self.setup.record(); self.assertEqual(record['source']['provider'],'football-data.co.uk')
        self.assertEqual(record['matching_rule_version'],'football-data-exact-v1')

    def test_performance_consumes_saved_outcomes_without_prediction_mutation(self):
        api=api_tests.ProspectiveApiTests();api.setUp();self.addCleanup(api.doCleanups)
        prediction_before=api.state_bytes()
        outcomes.record_sportmonks_outcome(state_dir=api.state,analysis_id=api.analysis_id,
            evidence=self.saved.evidence(),idempotency_key='performance-final')
        response=api.client.get('/api/v1/prospective/performance')
        self.assertEqual(response.status_code,200,response.text)
        self.assertGreater(response.json()['model_performance']['settled_prediction_runs'],0)
        after=api.state_bytes()
        for name,raw in prediction_before.items():self.assertEqual(after[name],raw)

    def cli_files(self, folder):
        response=json.dumps({'data':[self.saved.row],'pagination':{'has_more':False,'next_page':None}}).encode()
        source=Path(folder)/'response';source.write_bytes(response)
        calendar=Path(folder)/'calendar';calendar.write_bytes(self.saved.calendar)
        manifest={'version':1,'provider':'sportmonks','competition':'E1',
            'capture_reference':'saved-capture-001','response_sha256':hashlib.sha256(response).hexdigest(),
            'calendar_sha256':hashlib.sha256(self.saved.calendar).hexdigest(),
            'retrieved_at_utc':'2026-10-09T22:00:00+00:00','season_id':27903,
            'fixture_id':self.saved.row['id'],'team_bindings':self.saved.bindings}
        return source,calendar,manifest

    def invoke_cli(self, folder, manifest):
        from contextlib import redirect_stdout
        from io import StringIO
        from modelfc.sportmonks_outcome_evidence import main
        path=Path(folder)/'manifest';raw=json.dumps(manifest).encode();path.write_bytes(raw)
        output=StringIO()
        with patch('modelfc.providers.sportmonks_results.SportmonksRecentResultsClient.fetch',
                   side_effect=AssertionError('network forbidden')),redirect_stdout(output):
            code=main(['--manifest',str(path),'--manifest-sha256',hashlib.sha256(raw).hexdigest(),
                '--response-file',str(Path(folder)/'response'),'--calendar-file',str(Path(folder)/'calendar'),
                '--state-dir',str(self.setup.state),'--analysis-id',self.setup.analysis_id,'--key','cli-final'])
        self.assertNotIn(folder,output.getvalue())
        return code,json.loads(output.getvalue())

    def test_cli_settles_and_reuses_without_network_or_mutating_capture(self):
        with TemporaryDirectory() as folder:
            _,_,manifest=self.cli_files(folder)
            for status in ('RECORDED','REUSED'):
                code,receipt=self.invoke_cli(folder,manifest)
                self.assertEqual(code,0);self.assertEqual(receipt['status'],status)
                self.assertEqual(receipt['provider_requests'],0)
            original=(self.setup.outdir/next(self.setup.outdir.glob('*.json')).name).read_bytes()
            self.saved.row['statistics'][0]['data']['value']=7
            _,_,conflicting=self.cli_files(folder)
            code,receipt=self.invoke_cli(folder,conflicting)
            self.assertEqual(code,1)
            self.assertEqual(receipt,{'status':'CONFLICT','reason':'IDEMPOTENCY_CONFLICT','provider_requests':0})
            self.assertEqual(next(self.setup.outdir.glob('*.json')).read_bytes(),original)
        self.assertEqual(self.setup.capture_path.read_bytes(),self.setup.original)

    def test_cli_malformed_manifest_timestamp_is_sanitized_before_writes(self):
        with TemporaryDirectory() as folder:
            _,_,manifest=self.cli_files(folder)
            for value in (None,5,{},'private-secret-invalid-date'):
                manifest['retrieved_at_utc']=value
                code,receipt=self.invoke_cli(folder,manifest)
                self.assertEqual(code,1)
                self.assertEqual(receipt,{'status':'REJECTED','reason':'SAVED_RESULT_SETTLEMENT_REJECTED',
                    'provider_requests':0});self.assertNotIn('private-secret',json.dumps(receipt))
        self.assertFalse(self.setup.outdir.exists())

    def test_cli_rejects_missing_private_evidence_without_leakage_or_provider_calls(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from modelfc.sportmonks_outcome_evidence import main
        output=StringIO()
        with patch('modelfc.providers.sportmonks_results.SportmonksRecentResultsClient.fetch',
                   side_effect=AssertionError('network forbidden')), redirect_stdout(output):
            code=main(['--manifest','/private/credential-token','--manifest-sha256','0'*64,
                '--response-file','/private/response','--calendar-file','/private/calendar',
                '--state-dir',str(self.setup.state),'--analysis-id',self.setup.analysis_id,'--key','final'])
        self.assertEqual(code,1)
        self.assertEqual(json.loads(output.getvalue()),{'status':'REJECTED',
            'reason':'SAVED_RESULT_SETTLEMENT_REJECTED','provider_requests':0})
        self.assertNotIn('/private',output.getvalue());self.assertFalse(self.setup.outdir.exists())

    def test_pinned_reader_rejects_links_hash_mismatch_and_missing(self):
        with TemporaryDirectory() as folder:
            source=Path(folder)/'source';source.write_bytes(b'redacted')
            digest=hashlib.sha256(b'redacted').hexdigest()
            self.assertEqual(read_pinned(source,digest,20),b'redacted')
            link=Path(folder)/'link';link.symlink_to(source)
            for path,sha,limit in ((link,digest,20),(source,'0'*64,20),
                (Path(folder)/'missing',digest,20),(source,digest,2)):
                with self.subTest(path=path),self.assertRaises(ValueError):read_pinned(path,sha,limit)
