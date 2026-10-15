import copy
import json
import logging
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from wealth_advisor.agents import AdvisoryAgent, AnalyzerAgent, DataFetcherAgent, Orchestrator
from wealth_advisor.analytics import FinancialDataTool, RuleAnalysisTool
from wealth_advisor.contracts import InputError, ToolError
from wealth_advisor.mock_crm import Handler
from wealth_advisor.tools import HttpCRMTool, MockCRMTool, SQLiteMemory

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def server(handler):
    http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}'
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = json.loads((ROOT / 'samples/client.json').read_text(encoding='utf-8-sig'))
        self.memory = SQLiteMemory(Path(self.tmp.name) / 'test.sqlite3')

    def system(self, crm=None, memory=None):
        return Orchestrator(DataFetcherAgent(FinancialDataTool(), crm or MockCRMTool()),
                            AnalyzerAgent(RuleAnalysisTool()), AdvisoryAgent(), memory or self.memory)

    def test_full_flow_and_evidence(self):
        result = self.system().run(self.raw)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(result['risk_level'], 'high')
        self.assertEqual(result['analysis']['metrics']['monthly_surplus'], -1200)
        signals = {s['code']: s for s in result['analysis']['signals']}
        self.assertEqual(set(signals), {'NEGATIVE_CASH_FLOW', 'LOW_LIQUIDITY', 'CONCENTRATION',
                                       'UNUSUAL_OUTFLOW', 'SPENDING_INCREASE'})
        self.assertEqual(signals['UNUSUAL_OUTFLOW']['evidence']['transaction_id'], 'T08')
        self.assertEqual(result['analysis']['metrics']['prior_30d_outflows'], 520)
        self.assertEqual(self.memory.get(result['run_id']), result)
        with self.memory.connect() as conn:
            row = conn.execute('SELECT raw,cleaned,curated FROM runs').fetchone()
        self.assertEqual(json.loads(row[0]), self.raw)
        self.assertIn('quality', json.loads(row[1]))
        self.assertIn('crm', json.loads(row[2]))

    def test_memory_reuse_and_client_isolation(self):
        first = self.system().run(self.raw)
        second = self.system().run(self.raw)
        self.assertEqual(second['previous_summary']['run_id'], first['run_id'])
        self.assertIn('UNUSUAL_OUTFLOW', second['recurring_signal_codes'])
        self.raw['client_id'] = 'OTHER'
        self.assertIsNone(self.system().run(self.raw)['previous_summary'])

    def test_crm_outage_degrades_without_inventing_profile(self):
        result = self.system(crm=MockCRMTool(fail=True)).run(self.raw)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(result['crm_context']['risk_tolerance'], 'unknown')
        self.assertTrue(result['warnings'])

    def test_missing_input_fails_without_persisting(self):
        result = self.system().run({'client_id': 'bad'})
        self.assertEqual(result['error']['code'], 'INVALID_INPUT')
        with self.memory.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_bad_values_and_types(self):
        for value in [-1, float('nan'), float('inf'), True, None, [], '1.001']:
            with self.subTest(value=value):
                self.raw['monthly_income'] = value
                self.assertEqual(self.system().run(self.raw)['status'], 'failed')
        for raw in [None, [], 'input']:
            self.assertEqual(self.system().run(raw)['error']['code'], 'INVALID_INPUT')

    def test_invalid_transaction_and_currency(self):
        for change in [{'date': '2099-01-01'}, {'date': 'oops'}, {'direction': 'debit'}, {'amount': -1}]:
            raw = copy.deepcopy(self.raw)
            raw['transactions'][0].update(change)
            self.assertEqual(self.system().run(raw)['status'], 'failed')
        self.raw['currency'] = 'IDR'
        self.assertEqual(self.system().run(self.raw)['status'], 'failed')

    def test_strict_date_contract(self):
        for invalid in ['20261015', '2026-W42-4', '2026-02-30', None]:
            with self.subTest(date=invalid):
                raw = copy.deepcopy(self.raw)
                raw['as_of'] = invalid
                self.assertEqual(self.system().run(raw)['error']['code'], 'INVALID_INPUT')
                raw = copy.deepcopy(self.raw)
                raw['transactions'][0]['date'] = invalid
                self.assertEqual(self.system().run(raw)['error']['code'], 'INVALID_INPUT')

    def test_liquidity_threshold_uses_unrounded_amounts(self):
        self.raw['monthly_expenses'] = 1000
        for assets, expected in [(2999.99, True), (3000, False), (3000.01, False)]:
            with self.subTest(assets=assets):
                self.raw['liquid_assets'] = assets
                result = self.system().run(self.raw)
                codes = [signal['code'] for signal in result['analysis']['signals']]
                self.assertEqual('LOW_LIQUIDITY' in codes, expected)

    def test_duplicate_policy(self):
        self.raw['transactions'].append(copy.deepcopy(self.raw['transactions'][0]))
        result = self.system().run(self.raw)
        self.assertEqual(result['data_quality']['duplicates_removed'], 1)
        self.assertEqual(result['data_quality']['transaction_count'], 9)
        self.raw['transactions'][-1]['amount'] = 999
        self.assertEqual(self.system().run(self.raw)['status'], 'failed')

    def test_empty_history_and_zero_denominators(self):
        self.raw.update(transactions=[], holdings=[], monthly_income=0, monthly_expenses=0)
        result = self.system().run(self.raw)
        self.assertEqual(result['risk_level'], 'low')
        self.assertIsNone(result['analysis']['metrics']['liquidity_runway_months'])
        self.assertGreaterEqual(len(result['analysis']['limitations']), 3)
        json.dumps(result, allow_nan=False)

    def test_zero_mad_still_detects_large_outflow(self):
        for tx in self.raw['transactions']:
            tx['amount'] = 100
        self.raw['transactions'][7]['amount'] = 9000
        signals = self.system().run(self.raw)['analysis']['signals']
        outliers = [s for s in signals if s['code'] == 'UNUSUAL_OUTFLOW']
        self.assertEqual(len(outliers), 1)
        self.assertEqual(outliers[0]['evidence']['mad'], 0)

    def test_window_boundaries(self):
        from datetime import date, timedelta
        as_of = date.fromisoformat(self.raw['as_of'])
        self.raw['transactions'] = [
            {'id': str(age), 'date': (as_of - timedelta(days=age)).isoformat(),
             'direction': 'outflow', 'amount': amount}
            for age, amount in [(0, 1), (29, 2), (30, 4), (59, 8), (60, 16)]
        ]
        metrics = self.system().run(self.raw)['analysis']['metrics']
        self.assertEqual(metrics['current_30d_outflows'], 3)
        self.assertEqual(metrics['prior_30d_outflows'], 12)

    def test_constant_baseline_does_not_flag_normal_amounts(self):
        for tx in self.raw['transactions']:
            tx['amount'] = 100
        result = self.system().run(self.raw)
        self.assertNotIn('UNUSUAL_OUTFLOW', [s['code'] for s in result['analysis']['signals']])

    def test_manual_decisions_preserve_evidence_and_are_final(self):
        for decision, status in [('approve', 'approved'), ('reject', 'rejected'), ('override', 'overridden')]:
            original = self.system().run(self.raw)
            reviewed = self.memory.review(original['run_id'], 'Demo Advisor', decision, 'Verified with client.')
            self.assertEqual(reviewed['status'], status)
            self.assertEqual(original['analysis'], reviewed['analysis'])
            with self.assertRaises(ValueError):
                self.memory.review(original['run_id'], 'Other', 'approve', 'Second decision')
        with self.memory.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM reviews').fetchone()[0], 3)

    def test_invalid_review(self):
        result = self.system().run(self.raw)
        for reviewer, decision, note in [('', 'approve', 'reason'), ('A', 'approve', ''), ('A', 'invalid', 'reason')]:
            with self.assertRaises(ValueError):
                self.memory.review(result['run_id'], reviewer, decision, note)
        with self.assertRaises(ValueError):
            self.memory.review('unknown', 'A', 'approve', 'reason')

    def test_memory_failure_modes(self):
        with patch.object(self.memory, 'previous', side_effect=sqlite3.OperationalError):
            result = self.system().run(self.raw)
        self.assertEqual(result['status'], 'pending_review')
        self.assertTrue(result['warnings'])
        with patch.object(self.memory, 'save', side_effect=sqlite3.OperationalError):
            result = self.system().run(self.raw)
        self.assertEqual(result['status'], 'persistence_failed')

    def test_unexpected_agent_failure_is_contained(self):
        system = self.system()
        with patch.object(system.analyzer, 'execute', side_effect=RuntimeError('private detail')):
            result = system.run(self.raw)
        self.assertEqual(result['error']['code'], 'EXECUTION_ERROR')
        self.assertNotIn('private detail', json.dumps(result))

    def test_structured_logging_does_not_include_financial_payload(self):
        with self.assertLogs('wealth_advisor', level=logging.INFO) as logs:
            self.system().run(self.raw)
        for record in logs.records:
            self.assertIn('run_id', json.loads(record.message))
        self.assertNotIn('DEMO-001', str(logs.output))
        self.assertNotIn('monthly_income', str(logs.output))


class HTTPTests(unittest.TestCase):
    def test_real_http_success_404_and_retries(self):
        with server(Handler) as url:
            tool = HttpCRMTool(url)
            self.assertEqual(tool.fetch('DEMO-001')['risk_tolerance'], 'balanced')
            with self.assertRaises(ToolError):
                tool.fetch('missing')
            self.assertEqual(tool.last_attempts, 1)
            with self.assertRaises(ToolError):
                tool.fetch('unavailable')
            self.assertEqual(tool.last_attempts, 3)

    def test_malformed_response_and_identity_mismatch(self):
        class BadHandler(Handler):
            body = b'not json'
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(self.body)
        with server(BadHandler) as url:
            with self.assertRaises(ToolError):
                HttpCRMTool(url).fetch('A')
            BadHandler.body = b'{"client_id":"B","risk_tolerance":"growth"}'
            with self.assertRaises(ToolError):
                HttpCRMTool(url).fetch('A')

    def test_timeout_budget(self):
        tool = HttpCRMTool('http://127.0.0.1:9999', attempts=2, timeout=.01)
        with patch('wealth_advisor.tools.urlopen', side_effect=TimeoutError) as request:
            with self.assertRaises(ToolError):
                tool.fetch('A')
        self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
