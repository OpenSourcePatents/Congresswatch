"""Regressions for reconciling the observed partial SEC run, offline."""
import unittest
from unittest.mock import patch

from fetch_finance import fetch_edgar_signals
from validation.reconcile_finance import classify, reconcile_record, verified_manifest
from sec_identity import eligible_sec_signals


class FinanceReconciliation(unittest.TestCase):
    def test_partial_hit_preserves_pair_like_reviewed_ingestor(self):
        prior = {'edgar_cik':'0001849253','corporate_insider_signals':83,
                 'edgar_updated':'last-good-recorded-time'}
        incoming = {'id':'R000579','edgar_cik':'0001897386','corporate_insider_signals':9}
        trace = {'log_lines':['SEC search failed for alias: 500',
                              'EDGAR Hit: 9 filings via CIK 0001897386']}
        result = reconcile_record(incoming, prior, trace)
        expected = dict(prior)
        with patch('fetch_finance.resolve_member_cik',return_value={'status':'query_failed','cik':None}):
            self.assertIsNone(fetch_edgar_signals(expected))
        for key in ('edgar_cik','corporate_insider_signals','edgar_updated','edgar_status','edgar_data_status'):
            self.assertEqual(result[key], expected[key])
        self.assertEqual(eligible_sec_signals(result),0)

    def test_partial_zero_and_count_failure_preserve_pair(self):
        prior = {'edgar_cik':'0001234567','corporate_insider_signals':26}
        for lines in [['SEC search failed: 500','EDGAR: unresolved'],['EDGAR query failed: 500']]:
            with self.subTest(lines=lines):
                result = reconcile_record({'edgar_cik':None,'corporate_insider_signals':0},prior,{'log_lines':lines})
                self.assertEqual(result['corporate_insider_signals'],26)
                self.assertEqual(result['edgar_cik'],'0001234567')
                self.assertEqual(result['edgar_data_status'],'stale')
                self.assertNotIn('edgar_updated',result)

    def test_successful_count_stays_unverified(self):
        incoming={'edgar_cik':'0001234567','corporate_insider_signals':9}
        result=reconcile_record(incoming,{}, {'log_lines':['EDGAR Hit: 9 filings via CIK 0001234567']})
        self.assertEqual(result['edgar_data_status'],'available')
        self.assertEqual(result['edgar_status'],'unverified_auto')
        self.assertEqual(eligible_sec_signals(result),0)
        with self.assertRaises(AssertionError):
            reconcile_record(incoming,{}, {'log_lines':['EDGAR Hit: 8 filings via CIK 0001234567']})

    def test_clean_unresolved_is_distinct_from_failure(self):
        result=reconcile_record({'corporate_insider_signals':0},{},{'log_lines':['EDGAR: unresolved']})
        self.assertEqual(result['edgar_data_status'],'unresolved')
        self.assertIsNone(result['edgar_cik'])
        with self.assertRaises(ValueError):classify([])
        with self.assertRaises(AssertionError):
            reconcile_record({}, {'edgar_identity_status':'verified'}, {'log_lines':['EDGAR: unresolved']})

    def test_actual_manifest_preserves_rejected_observations(self):
        data=verified_manifest()
        r=data['members']['R000579']
        self.assertEqual(r['incoming_detail']['edgar_cik'],'0001897386')
        self.assertEqual(r['incoming_detail']['corporate_insider_signals'],9)
        self.assertEqual(r['prior_detail']['corporate_insider_signals'],83)
        self.assertEqual(r['classification'],'query_failed')
        self.assertEqual(data['ryan_candidate_official_response']['name'],'Thompson Patrick Ryan')
        for bid in ['M001210','S001195','M001238','S001184']:
            r=data['members'][bid]
            self.assertEqual(r['incoming_detail']['corporate_insider_signals'],0)
            self.assertEqual(r['classification'],'query_failed')
            self.assertGreater(r['prior_detail']['corporate_insider_signals'],0)


if __name__=='__main__':unittest.main()
