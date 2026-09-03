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
