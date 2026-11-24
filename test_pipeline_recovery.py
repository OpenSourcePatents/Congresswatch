import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import fetch_senate_efd as senate
import fetch_travel_costs as travel


def response(status):
    r = requests.Response()
    r.status_code = status
    r._content = b''
    r.raw = io.BytesIO(b'')
    r.url = senate.EFD_HOME
    return r


class PipelineRecoveryTests(unittest.TestCase):
    def test_gate_recovers_after_forbidden(self):
        client = senate.SenateEFDClient()
        with patch.object(client.session, 'request', side_effect=[response(403), response(200)]) as request, patch.object(senate.time, 'sleep') as sleep:
            self.assertEqual(client._request('GET', senate.EFD_HOME, retry_forbidden=True).status_code, 200)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(sleep.call_count, 1)

    def test_gate_exhaustion_preserves_http_status(self):
        client = senate.SenateEFDClient()
        output = io.StringIO()
        with patch.object(client.session, 'request', side_effect=[response(403) for _ in range(3)]), patch.object(senate.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            self.assertFalse(client.accept_agreement())
            self.assertEqual(sleep.call_count, 2)
        self.assertIn('home page: 403', output.getvalue())

    def test_timeout_is_bounded(self):
        client = senate.SenateEFDClient()
        with patch.object(client.session, 'request', side_effect=requests.Timeout('timeout')) as request, patch.object(senate.time, 'sleep'):
            self.assertFalse(client.accept_agreement())
            self.assertEqual(request.call_count, 3)

    def run_travel(self, status):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        details = root / 'details'
        details.mkdir()
        doc = details / 'member.json'
        original = json.dumps({'travel': [{'source_doc': 'https://disclosures-clerk.house.gov/test.pdf'}]})
        doc.write_text(original)
        manifest = root / 'manifest.json'
        with patch.object(travel, 'DETAILS_DIR', str(details)), patch.object(travel, 'MANIFEST_PATH', str(manifest)), patch.object(travel.requests, 'get', return_value=response(status)):
            if status == 503:
                with self.assertRaises(SystemExit):
                    travel.main()
            else:
                travel.main()
            state = json.loads(manifest.read_text())
            self.assertEqual(doc.read_text(), original)
            if status == 404:
                self.assertEqual(travel.collect_candidates(state), [])
                entry = next(iter(state.values()))
                entry['retry_after'] = '2000-01-01T00:00:00+00:00'
                self.assertEqual(len(travel.collect_candidates(state)), 1)
        return next(iter(state.values()))['status']

    def test_missing_document_is_deferred_without_data_loss(self):
        self.assertEqual(self.run_travel(404), 'document_missing')

    def test_server_outage_still_fails(self):
        self.assertEqual(self.run_travel(503), 'download_error')


class FinanceReconciliationTests(unittest.TestCase):
    def setUp(self):
        from validation.reconcile_nov24_finance import reconciled_sec
        self.reconcile = reconciled_sec
        self.prior = {'edgar_cik': '0001382475', 'corporate_insider_signals': 19,
                      'edgar_identity_status': 'unverified', 'edgar_updated': 'last-good'}

    def test_failed_new_cik_cannot_inherit_old_count(self):
        obs = {'alias_errors': [], 'query_errors': ['HTTP 500'], 'hit': None}
        result = self.reconcile(self.prior, obs, 'attempt')
        self.assertEqual((result['edgar_cik'], result['corporate_insider_signals']),
                         ('0001382475', 19))
        self.assertEqual(result['edgar_updated'], 'last-good')
        self.assertEqual(result['edgar_data_status'], 'stale')

    def test_partial_alias_success_is_raw_only(self):
        obs = {'alias_errors': ['HTTP 500'], 'query_errors': [],
               'hit': {'cik': '0002021757', 'count': 11}}
        result = self.reconcile(self.prior, obs, 'attempt')
        self.assertEqual(result['edgar_cik'], self.prior['edgar_cik'])
        self.assertEqual(result['corporate_insider_signals'], 19)
        self.assertEqual(result['edgar_status'], 'query_failed')

    def test_complete_raw_retrieval_never_verifies_identity(self):
        obs = {'alias_errors': [], 'query_errors': [],
               'hit': {'cik': '0001852313', 'count': 12}}
        result = self.reconcile(self.prior, obs, 'attempt')
        self.assertEqual((result['edgar_cik'], result['corporate_insider_signals']),
                         ('0001852313', 12))
        self.assertEqual(result['edgar_status'], 'unverified_auto')
        from fetch_finance import eligible_sec_signals
        self.assertEqual(eligible_sec_signals({**self.prior, **result}), 0)

    def test_verified_input_requires_separate_review(self):
        prior = {**self.prior, 'edgar_identity_status': 'verified'}
        with self.assertRaisesRegex(ValueError, 'Verified identity'):
            self.reconcile(prior, {'alias_errors': ['HTTP 500']}, 'attempt')


if __name__ == '__main__':
    unittest.main()
