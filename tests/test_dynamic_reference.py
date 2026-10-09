import copy
import json
import unittest
from pathlib import Path
from harness.web_fetch.dynamic_reference import entry_facts
from harness.web_fetch.metrics import evaluate_content

ROOT=Path(__file__).resolve().parents[1]

class DynamicReferenceTests(unittest.TestCase):
    def test_all_observed_fields_are_required(self):
        captures=json.loads((ROOT/'research/dynamic-browser-captures.json').read_text())
        captures+=json.loads((ROOT/'research/dynamic-expansion-captures.json').read_text())
        for c in captures:
            facts=entry_facts(c['id'],c['entries'])
            ref=c['rendered_text']
            score=lambda text:evaluate_content(text,[],required_facts=facts,min_content_chars=1)
            self.assertTrue(score(ref)['content_pass'],c['id'])
            # Delete each occurrence: duplicated fields cannot mask missing field coverage.
            for fact in facts:
                value=fact['alternatives'][0]
                self.assertFalse(score(ref.casefold().replace(value.casefold(),''))['content_pass'],(c['id'],fact['id']))

    def test_placeholder_or_incomplete_card_is_rejected(self):
        for entries in [[''],['Only a product name']]:
            with self.assertRaises(ValueError):entry_facts('dynamic-products-api',entries)

    def test_count_label_spacing_preserves_numeric_identity(self):
        facts=entry_facts('dynamic-catalog',['$10\nTitle\nDescription\n1 reviews'])
        score=lambda text:evaluate_content(text,[],required_facts=facts,min_content_chars=1)['content_pass']
        self.assertTrue(score('$10 Title Description 1reviews'))
        self.assertFalse(score('$10 Title Description 11reviews'))

    def test_new_observed_values_replace_old_fact_values(self):
        # Snapshot diagnostics can describe a new independent sample, without
        # silently changing the separately reviewed stable acceptance contract.
        captures = json.loads((ROOT/'research/dynamic-expansion-captures.json').read_text())
        case = next(c for c in captures if c['id'] == 'dynamic-iframe')
        updated = copy.deepcopy(case['entries'])
        old = updated[0]
        updated[0] = 'Updated family name'
        facts = entry_facts(case['id'], updated)
        fresh = '\n'.join(updated)
        stale = '\n'.join(case['entries'])
        score = lambda text: evaluate_content(text, [], required_facts=facts, min_content_chars=1)
        self.assertTrue(score(fresh)['content_pass'])
        self.assertFalse(score(stale)['content_pass'])
        self.assertNotEqual(old, updated[0])
