"""The focused acceptance policy must never turn missing coverage into PASS."""
import io
import importlib.util
import socket
import unittest
from unittest.mock import patch

from ops.vps import deployment_acceptance as acceptance


class AcceptancePolicyTests(unittest.TestCase):
    def exercise(self, method):
        case_type = type('Sample', (unittest.TestCase,), {'test_sample': method})
        test = case_type('test_sample')
        with patch.object(acceptance, 'TEST_IDS', (test.id(),)), patch.object(
                acceptance, 'EXPECTED_COUNT', 1):
            return acceptance.run_suite(unittest.TestSuite([test]), stream=io.StringIO())

    def test_only_complete_success_passes(self):
        self.assertTrue(self.exercise(lambda self: None))
        self.assertFalse(self.exercise(lambda self: self.fail('fixture failure')))
        self.assertFalse(self.exercise(lambda self: (_ for _ in ()).throw(RuntimeError('fixture error'))))
        self.assertFalse(self.exercise(lambda self: self.skipTest('required probe unavailable')))
        self.assertFalse(self.exercise(unittest.expectedFailure(lambda self: self.fail('known failure'))))
        self.assertFalse(self.exercise(unittest.expectedFailure(lambda self: None)))

    def test_missing_duplicate_and_empty_inventory_reject(self):
        for ids in ((), ('missing.module.case',), (acceptance.TEST_IDS[0],)*2):
            with self.subTest(ids=ids), patch.object(acceptance, 'TEST_IDS', ids):
                with self.assertRaises(ValueError):
                    acceptance.load_suite()
        with patch.object(acceptance, 'TEST_IDS', ('missing.module.case',)), patch.object(
                acceptance, 'EXPECTED_COUNT', 1), self.assertRaises(ValueError):
            acceptance.load_suite()

    def test_incomplete_run_cannot_pass(self):
        class Sample(unittest.TestCase):
            def test_sample(self): pass
        class Incomplete(unittest.TestSuite):
            def run(self, result, debug=False):
                return result  # No error, but no acceptance coverage either.
        test = Sample('test_sample')
        with patch.object(acceptance, 'TEST_IDS', (test.id(),)), patch.object(
                acceptance, 'EXPECTED_COUNT', 1):
            self.assertFalse(acceptance.run_suite(Incomplete([test]), stream=io.StringIO()))

    def test_fixed_inventory_loads_individual_methods(self):
        suite = acceptance.load_suite()
        self.assertEqual(tuple(case.id() for case in acceptance.cases(suite)), acceptance.TEST_IDS)
        self.assertEqual(suite.countTestCases(), 14)

    def test_no_subset_arguments_or_socket_access(self):
        with patch.object(acceptance.sys, 'argv', ['acceptance', '--subset']), patch.object(
                acceptance, 'load_suite') as load:
            self.assertEqual(acceptance.main(), 1)
            load.assert_not_called()
        def attempt_network():
            socket.getaddrinfo('provider.invalid', 443)
        with patch.object(acceptance.sys, 'argv', ['acceptance']), patch.object(
                acceptance, 'load_suite', side_effect=attempt_network), patch.object(
                acceptance.sys, 'stderr', io.StringIO()):
            self.assertEqual(acceptance.main(), 1)

    def test_host_probes_are_explicit_before_optional_dependency_graph(self):
        from tests import test_release_environment
        self.assertEqual(unittest.TestLoader().loadTestsFromModule(test_release_environment).countTestCases(), 0)
        self.assertTrue(all(name.startswith('tests.test_release_environment.')
                            for name in acceptance.TEST_IDS[:4]))

    def test_installed_result_instrumentation_is_preserved(self):
        observed = []
        class TimedResult(unittest.TextTestResult):
            def startTest(self, test):
                observed.append(('start', test.id()))
                super().startTest(test)
            def stopTest(self, test):
                observed.append(('stop', test.id()))
                super().stopTest(test)
        spec = importlib.util.spec_from_file_location('acceptance_fixture', acceptance.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.object(unittest.TextTestRunner, 'resultclass', TimedResult):
            spec.loader.exec_module(module)
        class Sample(unittest.TestCase):
            def test_sample(self): pass
        test = Sample('test_sample')
        module.TEST_IDS = (test.id(),)
        module.EXPECTED_COUNT = 1
        self.assertTrue(module.run_suite(unittest.TestSuite([test]), stream=io.StringIO()))
        self.assertEqual(observed, [('start', test.id()), ('stop', test.id())])
