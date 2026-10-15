"""Runner contract tests using synthetic authenticated observations; no host calls."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from ops.github.readiness_consumer import ReadinessSession, ReleaseRun, REQUIRED_JOBS

SHA, REV = 'a' * 40, 'b' * 64
RUN = ReleaseRun('stevengalvis/modelfc', SHA, 123, 2, 'push', 'refs/heads/main')
JOBS = dict.fromkeys(REQUIRED_JOBS, 'success')


class ConsumerTests(unittest.TestCase):
    def session(self, **kw):
        return ReadinessSession(RUN, JOBS, REV, monotonic_now=0, **kw)

    def begin(self, session, now=1000, tick=1):
        return session.begin(current_main_sha=SHA, now=now, monotonic_now=tick)

    def proof(self, request, **updates):
        value = dict(schema_version=1, requested_sha=SHA, nonce=request['nonce'],
            helper_sha256=REV, observed_at=1001, status='PASS', reason='READY',
            serving_sha=SHA, pid=1234, start_ticks=5678, invocation_id='c'*32,
            capabilities_http_200=True, identity_stable=True)
        value.update(updates)
        return json.dumps(value).encode()

    def accept(self, session, raw, **updates):
        args = dict(current_main_sha=SHA, now=1002, monotonic_now=3)
        args.update(updates)
        return session.accept(raw, **args)

    def finish(self, session, **updates):
        args = dict(current_main_sha=SHA, now=1003, monotonic_now=4)
        args.update(updates)
        return session.finish(**args)

    def test_complete_run_bound_one_shot_result(self):
        session = self.session(); request = self.begin(session)
        self.accept(session, self.proof(request))
        result = self.finish(session)
        self.assertEqual((result['sha'], result['run_id'], result['run_attempt']), (SHA, 123, 2))
        self.assertEqual(result['evidence']['nonce'], request['nonce'])
        with self.assertRaises(ValueError): self.finish(session)
        with self.assertRaises(ValueError): self.begin(session, tick=5)

    def test_every_prerequisite_must_succeed_on_push_main(self):
        for job in JOBS:
            for state in ('failure', 'skipped', 'cancelled', None, 'neutral', 'in_progress'):
                with self.subTest(job=job, state=state), self.assertRaises(ValueError):
                    ReadinessSession(RUN, dict(JOBS, **{job:state}), REV, monotonic_now=0)
            missing = dict(JOBS); del missing[job]
            with self.assertRaises(ValueError): ReadinessSession(RUN, missing, REV, monotonic_now=0)
        for field, value in (('repository','other/repo'), ('sha','bad'), ('run_id',True),
                ('run_attempt',0), ('event','pull_request'), ('ref','refs/heads/other')):
            with self.assertRaises(ValueError):
                ReadinessSession(replace(RUN, **{field:value}), JOBS, REV, monotonic_now=0)
        with self.assertRaises(ValueError): ReadinessSession(RUN, JOBS, 'bad', monotonic_now=0)

    def test_superseded_before_request_after_probe_and_before_publication(self):
        for stage in ('begin', 'accept', 'finish'):
            session = self.session()
            if stage != 'begin': request = self.begin(session)
            if stage == 'finish': self.accept(session, self.proof(request))
            args = dict(current_main_sha='d'*40, now=1003, monotonic_now=4)
            with self.assertRaises(ValueError):
                if stage == 'accept': session.accept(self.proof(request), **args)
                else: getattr(session, stage)(**args)
            with self.assertRaises(ValueError): self.begin(session, tick=5)

    def test_same_sha_other_session_and_old_challenge_cannot_pass(self):
        first, second = self.session(), self.session()
        old = self.begin(first); current = self.begin(second)
        self.assertNotEqual(old['nonce'], current['nonce'])
        with self.assertRaises(ValueError): self.accept(second, self.proof(old))
        with self.assertRaises(ValueError): self.accept(second, self.proof(current))
        newer = self.begin(first, tick=2)
        with self.assertRaises(ValueError): self.accept(first, self.proof(old))
        with self.assertRaises(ValueError): self.finish(first)
        self.assertNotEqual(newer['nonce'], old['nonce'])

    def test_missing_failed_malformed_oversize_wrong_pin_proof_cannot_finish(self):
        for bad in (None, b'', b'x'*4097, b'{"status":"PASS","status":"PASS"}',
                    {'status':'FAIL'}, {'helper_sha256':'f'*64}, {'identity_stable':False},
                    {'observed_at':800}, {'observed_at':1100}):
            session = self.session(); request = self.begin(session)
            raw = self.proof(request, **bad) if isinstance(bad, dict) else bad
            with self.assertRaises(ValueError): self.accept(session, raw)
            with self.assertRaises(ValueError): self.finish(session)

    def test_rotation_retry_requires_new_challenge_and_new_observation(self):
        session = self.session(); request = self.begin(session)
        with self.assertRaises(ValueError): self.accept(session, self.proof(request, status='FAIL'))
        request2 = self.begin(session, now=1030, tick=31)
        self.assertNotEqual(request['nonce'], request2['nonce'])
        self.accept(session, self.proof(request2, observed_at=1031), now=1032, monotonic_now=33)
        self.assertEqual(self.finish(session, now=1033, monotonic_now=34)['sha'], SHA)

    def test_publication_rechecks_freshness_and_begin_discards_pass(self):
        for action in ('stale', 'restart'):
            session = self.session(); request = self.begin(session)
            self.accept(session, self.proof(request))
            if action == 'restart': self.begin(session, now=1003, tick=4)
            with self.assertRaises(ValueError): self.finish(session, now=1121, monotonic_now=122)

    def test_monotonic_deadline_including_probe_and_publication_and_clock_reversal(self):
        for tick in (2100, 2101, 0, float('nan'), float('inf'), True):
            session = self.session(); request = self.begin(session)
            with self.assertRaises(ValueError): self.accept(session, self.proof(request), monotonic_now=tick)
        session = self.session(); request = self.begin(session)
        self.accept(session, self.proof(request))
        with self.assertRaises(ValueError): self.finish(session, monotonic_now=2100)

    def test_attempt_count_and_nonce_collision_fail_closed(self):
        session = self.session()
        for n in range(71): self.begin(session, tick=n+1)
        with self.assertRaises(ValueError): self.begin(session, tick=72)
        session = self.session()
        with patch('ops.github.readiness_consumer.secrets.token_hex', return_value='e'*64):
            self.begin(session)
            with self.assertRaises(ValueError): self.begin(session, tick=2)
        with self.assertRaises(ValueError): self.begin(session, tick=3)


if __name__ == '__main__':
    unittest.main()
