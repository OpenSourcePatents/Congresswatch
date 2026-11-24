"""Offline source/assembly failure checks; no Git writes or network."""
import copy
from datetime import datetime, timezone
from unittest import TestCase
from refresh_official_bills import ANALYSIS_VERSION, BASELINE, get, normalize, quarantine_inactive, validate_live_scope, validate_sources


class OfficialRefreshChecks(TestCase):
    def setUp(self):
        self.baseline = get(BASELINE)

    def test_baseline_complete(self):
        validate_sources(self.baseline, self.baseline)

    def test_duplicate_partition_refused(self):
        changed = copy.deepcopy(self.baseline)
        changed['partitions'][-1] = changed['partitions'][0]
        with self.assertRaisesRegex(SystemExit, 'missing or duplicate'):
            validate_sources(changed,self.baseline)

    def test_truncated_zip_refused_even_when_counts_agree(self):
        changed = copy.deepcopy(self.baseline)
        changed['partitions'][0]['indexed'] -= 1
        changed['partitions'][0]['counts']['parsed'] -= 1
        with self.assertRaisesRegex(SystemExit, 'incomplete or shrinking'):
            validate_sources(changed,self.baseline)

    def test_rollover_conservatively_stops_at_jan1_utc(self):
        validate_live_scope(119,119,datetime(2026,12,31,23,59,tzinfo=timezone.utc))
        with self.assertRaisesRegex(SystemExit,'rollover'):
            validate_live_scope(119,119,datetime(2027,1,1,tzinfo=timezone.utc))
        with self.assertRaisesRegex(SystemExit,'differs'):
            validate_live_scope(120,119,datetime(2026,12,31,tzinfo=timezone.utc))

    def test_existing_archive_and_nonbill_inputs_preserved(self):
        manual = {'bill_id':'legacy-manual','note':'keep this'}
        original = {'publication_status':'legacy_archive_only','original_fields':{'bills':[{'note':'previous'}]}}
        record = {'id':'A000001','bills':[manual], 'bill_history':[original],
                  'trades':[{'original':'trade'}],'votes':[{'original':'vote'}]}
        stage = {'id':'A000001','bills':[{'analysis_version':ANALYSIS_VERSION,'bill_id':'HR51-119'}]}
        normalize(record,stage,'manifest','checked')
        normalize(record,stage,'manifest','checked')
        self.assertEqual(record['bills'],stage['bills'])
        self.assertEqual(record['bill_history'][0], original)
        self.assertEqual(record['bill_history'][1]['original_fields']['bills'], [manual])
        self.assertEqual(len(record['bill_history']),2)
        self.assertEqual(record['trades'],[{'original':'trade'}])
        self.assertEqual(record['votes'],[{'original':'vote'}])

    def test_inactive_legacy_match_retained_only_as_reference(self):
        legacy = {'bill_id':'HR123-118','title':'historical','similar_member_bills':[{'legacy':True}],
                  'has_text':True,'alec_best_similarity':.99}
        record = {'bills':[legacy]}
        self.assertTrue(quarantine_inactive(record))
        self.assertFalse(quarantine_inactive(record))
        self.assertEqual(record['bill_history'][0]['original_fields']['bills'],[legacy])
        self.assertEqual(record['bills'][0]['title'],'historical')
        self.assertEqual(record['bills'][0]['similar_member_bills'],[])
        self.assertIsNone(record['bills'][0]['alec_best_similarity'])
