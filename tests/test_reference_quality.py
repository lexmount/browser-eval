import unittest
from harness.web_fetch.reference_quality import clean_reference_text,audit_reference
from harness.web_fetch.metrics import normalize_for_match
from harness.web_fetch.models import execution_id


class ReferenceQualityTests(unittest.TestCase):
    def test_cleaning_is_narrow_and_idempotent(self):
        raw='不通过 -10.5% C++ a\u200db\n正文\u200b\ufeff'
        clean=clean_reference_text(raw)
        self.assertEqual(clean,'不通过 -10.5% C++ a\u200db\n正文')
        self.assertEqual(clean_reference_text(clean),clean)
    def test_quality_gates(self):
        facts=[{'id':'a','alternatives':['absent']},{'id':'b','alternatives':['absent']}]
        issues=audit_reference('bad\ufffd\x00',facts,normalize=normalize_for_match)
        for expected in ['replacement_character','unexpected_control_character','fact_absent_from_reference:a','duplicate_fact:b']:
            self.assertIn(expected,issues)
    def test_short_valid_reference_is_not_rejected(self):
        self.assertEqual(audit_reference('Dummy PDF file',[{'id':'title','alternatives':['Dummy PDF file']}],normalize=normalize_for_match),[])
    def test_run_identifier_is_stable_and_distinguishes_retests(self):
        r={'attempt_id':'provider__case__r001','started_at':'2026-09-02T01:00:00Z'}
        self.assertEqual(execution_id(r),execution_id(dict(r)))
        self.assertNotEqual(execution_id(r),execution_id({**r,'started_at':'2026-09-02T02:00:00Z'}))
