"""Synthetic opt-in registration; no genuine-data acceptance."""
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc import five_league_readiness as shared
from modelfc.spain_italy_readiness import report as wrapper
from tests.test_spain_italy_readiness import evidence, NOW
from tests.test_five_league_readiness import e1, e0


def row(result, code):
    return next(r for r in result['leagues'] if r['competition'] == code)


class RegistrationTests(TestCase):
    def test_default_false_and_strict_opt_in(self):
        inputs = {'SP1': evidence()}
        default = shared.report(inputs, as_of=NOW)
        self.assertEqual(default, shared.report(inputs, as_of=NOW, spain_italy=False))
        self.assertNotIn('saved_calendar_adapter', row(default, 'SP1'))
        self.assertEqual(row(default, 'SP1')['markets']['btts']['gates']['calendar']['state'], 'UNSUPPORTED')
        for invalid in (None, 0, 1, 'true', [], {}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'OPT_IN_REJECTED'):
                shared.report(inputs, as_of=NOW, spain_italy=invalid)

    def test_shared_opt_in_matches_wrapper_with_blocked_qualification(self):
        inputs = {code: evidence(code) for code in ('SP1', 'I1')}
        enabled = shared.report(inputs, as_of=NOW, spain_italy=True)
        self.assertEqual(encoded(enabled), encoded(wrapper(inputs, as_of=NOW)))
        for code in inputs:
            for market in row(enabled, code)['markets'].values():
                self.assertEqual(market['gates']['calendar']['state'], 'VALID')
                self.assertEqual(market['gates']['mapping']['state'], 'VALID')
                self.assertEqual(market['input_state'], 'UNSUPPORTED')
                self.assertEqual(market['gates']['model']['reason'], 'MODEL_CONSUMER_UNQUALIFIED')
                self.assertFalse(market['validated']); self.assertFalse(market['live'])

    def test_mixed_league_and_market_states(self):
        sp = evidence(); name, data, _, at = sp.sources[0]
        data = data.replace(b'D,0,0,2,7', b'D,,,2,7')
        sp = replace(sp, sources=((name, data, fingerprint(data), at),))
        it = replace(evidence('I1'), mapping_sha256='0'*64)
        result = shared.report({'SP1': sp, 'I1': it}, as_of=NOW, spain_italy=True)
        markets = row(result, 'SP1')['markets']
        self.assertEqual(markets['corners']['gates']['coverage']['state'], 'MISSING')
        for market in ('btts', 'goal_totals'):
            self.assertEqual(markets[market]['gates']['coverage']['state'], 'VALID')
        self.assertEqual(row(result, 'I1')['markets']['btts']['gates']['calendar']['state'], 'INVALID')

    def test_e1_e0_mls_and_acquisition_limits_unchanged(self):
        pl, at = e0(); inputs = {'E0': pl, 'E1': e1(), 'SP1': evidence()}
        baseline = shared.report(inputs, as_of=at)
        enabled = shared.report(inputs, as_of=at, spain_italy=True)
        for code in ('E1', 'E0', 'MLS'):
            self.assertEqual(row(baseline, code), row(enabled, code))
        self.assertEqual(enabled['max_tournaments'], 5)
        self.assertEqual(enabled['provider_requests'], 0)
        self.assertFalse(enabled['live']); self.assertFalse(enabled['validated'])

    def test_single_quote_normalization_without_report_recursion(self):
        with patch.object(shared, '_quotes', wraps=shared._quotes) as normalize, patch(
                'socket.socket', side_effect=AssertionError('network')):
            wrapper({'SP1': evidence()}, as_of=NOW)
        self.assertEqual(normalize.call_count, 1)

    def test_contract_order_and_input_immutability(self):
        sp = evidence(); inputs = {'SP1': sp}
        first = shared.report(inputs, as_of=NOW, spain_italy=True)
        second = shared.report(inputs, as_of=NOW, spain_italy=True, contracts=tuple(reversed(shared.CONTRACTS)))
        self.assertEqual(encoded(first), encoded(second))
        self.assertEqual(inputs, {'SP1': sp})
