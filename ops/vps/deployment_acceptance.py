"""Fixed offline release acceptance; no installation or deployment operations.

Run from the immutable release root with its deployment-only virtualenv and
PYTHONPATH=src. The installed controller remains responsible for OS isolation,
exact-SHA admission, deadlines, descendant cleanup and promotion.
"""
import socket
import sys
import unittest
from unittest.mock import patch


# Individual methods, not discover/module/class selectors: additions to broad
# suites cannot silently change this deployment policy. Host probes run first.
TEST_IDS = (
    'tests.test_release_environment.ReleaseEnvironment.test_python_dependencies_and_import_origin',
    'tests.test_release_environment.ReleaseEnvironment.test_atomic_replace_fsync_and_no_follow',
    'tests.test_release_environment.ReleaseEnvironment.test_cross_process_lock_and_release',
    'tests.test_release_environment.ReleaseEnvironment.test_posix_acl_roundtrip',
    'tests.test_prospective_launch.ProspectiveLaunchTests.test_real_subprocess_prefers_pinned_src',
    'tests.test_prospective_end_to_end.ProspectiveProductionRehearsal.test_recorded_fixture_to_settled_api_and_performance',
    'tests.test_forecast_only_runner.ForecastOnlyRunnerTests.test_forecasts_are_persisted_without_odds_and_publicly_readable',
    'tests.test_forecast_only_runner.ForecastOnlyRunnerTests.test_replay_reuses_immutable_records_and_get_is_read_only',
    'tests.test_automatic_research_settlement.AutomaticSettlementTests.test_success_replay_new_acquisition_and_performance',
    'tests.test_pl_scheduled_caller.ScheduleTests.test_scheduled_publication_settlement_and_replay_preserve_prices',
    'tests.test_pl_scheduled_caller.ScheduleTests.test_disabled_missing_and_unscheduled_make_no_calls',
    'tests.test_pl_scheduled_caller.ScheduleTests.test_crash_after_outer_claim_never_automatically_resumes',
    'tests.test_pl_dispatch_acceptance.EndToEndAcceptance.test_stale_quote_and_wrong_result_fixture_fail_closed',
    'tests.test_pl_dispatch_acceptance.EndToEndAcceptance.test_interrupted_partial_freeze_cannot_retry_or_publish',
)
EXPECTED_COUNT = 14


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def load_suite():
    if len(TEST_IDS) != EXPECTED_COUNT or len(set(TEST_IDS)) != EXPECTED_COUNT:
        raise ValueError('acceptance inventory mismatch')
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(TEST_IDS)
    if loader.errors or tuple(test.id() for test in cases(suite)) != TEST_IDS:
        raise ValueError('acceptance tests missing, duplicated or substituted')
    return suite


# Inherit the installed bootstrap's timed result class when present. Do not
# replace its start/stop instrumentation or treat observations as pass evidence.
class StrictResult(unittest.TextTestRunner.resultclass):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_ids = []
        self.finished_ids = []

    def startTest(self, test):
        self.started_ids.append(test.id())
        super().startTest(test)

    def stopTest(self, test):
        self.finished_ids.append(test.id())
        super().stopTest(test)

    def addSkip(self, test, reason):
        self.addFailure(test, (AssertionError, AssertionError('required acceptance test skipped'), None))

    def addExpectedFailure(self, test, err):
        self.addFailure(test, (AssertionError, AssertionError('required acceptance test expected failure'), None))


def run_suite(suite, *, stream=None):
    expected = tuple(test.id() for test in cases(suite))
    if expected != TEST_IDS:
        raise ValueError('acceptance inventory mismatch')
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=StrictResult).run(suite)
    return (result.wasSuccessful() and result.testsRun == EXPECTED_COUNT
            and tuple(result.started_ids) == TEST_IDS and tuple(result.finished_ids) == TEST_IDS
            and not result.skipped and not result.expectedFailures)


def main():
    if len(sys.argv) != 1:
        return 1  # No caller-selectable subset, policy, mode or timeout.
    def offline(*args, **kwargs):
        raise RuntimeError('network access forbidden in offline acceptance')
    try:
        # Defense in depth for fixture regressions, not a replacement for the
        # installed PrivateNetwork boundary (nor coverage of child processes).
        with patch.object(socket.socket, 'connect', offline), patch.object(
                socket.socket, 'connect_ex', offline), patch.object(socket, 'getaddrinfo', offline):
            return 0 if run_suite(load_suite()) else 1
    except Exception:
        print('Release acceptance failed before completion', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
