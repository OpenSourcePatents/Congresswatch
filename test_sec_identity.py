"""Acceptance checks: SEC identity/retrieval and campaign/wealth independence."""
import copy
import unittest
from unittest.mock import Mock, patch

import requests
from fetch_finance import eligible_sec_signals, compute_score_components, resolve_member_cik
from sec_identity import retrieve_identity_evidence


class IdentityAcceptance(unittest.TestCase):
    def setUp(self):
        self.url = "https://www.sec.gov/example-reviewed-identity"
        self.member = {
            "id": "M001169", "name": "Test Member", "edgar_identity_status": "verified",
            "edgar_identity_cik": "0000000001", "edgar_cik": "0000000001",
            "edgar_identity_evidence": [self.url],
            "edgar_identity_evidence_checks": [{
                "url": self.url, "cik": "0000000001", "status": "available",
                "resolved_url": self.url, "sha256": "a" * 64,
                "checked_at": "2026-11-24T00:00:00+00:00"}],
            "edgar_status": "verified_manual", "edgar_data_status": "available",
            "corporate_insider_signals": 6,
        }

    def test_explicit_verified_retrieved_identity_positive(self):
        self.assertEqual(eligible_sec_signals(self.member), 6)
        self.assertEqual(compute_score_components(self.member)["trade_timing"], 25)

    def test_identity_and_retrieval_fail_closed(self):
        cases = [
            {"edgar_identity_status": "verified_auto"}, {"edgar_identity_cik": None},
            {"edgar_identity_cik": "0000000002"}, {"edgar_cik": None},
            {"edgar_identity_evidence": []}, {"edgar_identity_evidence": ["https://"]},
            {"edgar_identity_evidence": ["http://www.sec.gov/evidence"]},
            {"edgar_identity_evidence_checks": []},
            {"edgar_data_status": "stale"}, {"edgar_status": "query_failed"},
            {"corporate_insider_signals": float("nan")}, {"corporate_insider_signals": True},
        ]
        for change in cases:
            with self.subTest(change=change):
                record = dict(self.member, **change)
                self.assertEqual(eligible_sec_signals(record), 0)
                self.assertEqual(compute_score_components(record)["trade_timing"], 0)
                self.assertIs(record["corporate_insider_signals"], change.get("corporate_insider_signals", self.member["corporate_insider_signals"]))

    def test_receipts_bound_to_exact_cik_and_urls(self):
        for change in [{"cik": "2"}, {"url": "https://example.com/other"},
                       {"status": "unavailable"}, {"sha256": ""}, {"checked_at": ""},
                       {"resolved_url": "http://www.sec.gov/evidence"}]:
            record = copy.deepcopy(self.member)
            record["edgar_identity_evidence_checks"][0].update(change)
            self.assertEqual(eligible_sec_signals(record), 0)

    @patch("sec_identity.requests.get")
    def test_evidence_403_not_retried(self, get):
        response = Mock(status_code=403)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        get.return_value = response
        checks = retrieve_identity_evidence("0000000001", [self.url], {})
        self.assertEqual(checks[0]["status"], "unavailable")
        get.assert_called_once()

    @patch("sec_identity.requests.get")
    def test_retrieval_records_nonempty_content_hash(self, get):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b"reviewed identity evidence"]
        get.return_value = response
        checks = retrieve_identity_evidence("0000000001", [self.url], {})
        record = dict(self.member, edgar_identity_evidence_checks=checks)
        self.assertEqual(eligible_sec_signals(record), 6)

    @patch("fetch_finance.retrieve_identity_evidence")
    @patch("fetch_finance.load_json")
    def test_unretrievable_manual_assertion_is_preserved_but_ineligible(self, load, retrieve):
        load.return_value = {"M001169": {"identity_status": "verified", "cik": "0000000001",
                                        "evidence": [self.url]}}
        retrieve.return_value = [{"url": self.url, "cik": "0000000001", "status": "unavailable"}]
        record = dict(self.member)
        result = resolve_member_cik(record)
        self.assertEqual(result["cik"], "0000000001")
        self.assertEqual(record["edgar_identity_status"], "verification_unavailable")
        self.assertEqual(record["edgar_identity_assertion"], load.return_value["M001169"])
        self.assertEqual(eligible_sec_signals(record), 0)

    def test_campaign_money_never_becomes_wealth_or_reweights_other_components(self):
        base = dict(self.member, edgar_identity_status="unverified")
        expected = compute_score_components(base)
        for amount in [0, 1_000_000, 1_000_000_000, None]:
            record = dict(base, total_raised=amount, pac_contributions=amount,
                          term_start=1990, career_earnings=1_000_000)
            self.assertEqual(compute_score_components(record), expected)
            self.assertEqual(expected["wealth_gap"], 0)
            self.assertEqual(sum(expected.values()), 0)


if __name__ == "__main__":
    unittest.main()
