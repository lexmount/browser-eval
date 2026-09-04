import copy
import json
import unittest
from pathlib import Path

from harness.web_fetch.dynamic_entry_matching import matching_entry_facts
from harness.web_fetch.dynamic_reference import entry_facts
from harness.web_fetch.metrics import fact_in_text, normalize_for_match


ROOT = Path(__file__).resolve().parents[1]
from harness.web_fetch.storage import current_evidence_dir


class DynamicEntryMatchingTests(unittest.TestCase):
    def hits(self, text, facts):
        return matching_entry_facts(normalize_for_match(text), facts,
                                    normalize_for_match, fact_in_text)

    def assert_complete(self, text, facts):
        hits = self.hits(text, facts)
        self.assertEqual(set(hits), {f['id'] for f in facts})
        self.assertTrue(all(hits.values()), [k for k, v in hits.items() if not v])

    def test_corrupt_one_price_and_shipping_with_repeated_values(self):
        entries = ['First Shirt\n$10.90\nor 9 x$1.21\nFree shipping',
                   'Second Shirt\n$10.90\nor 9 x$1.21\nFree shipping']
        facts = entry_facts('dynamic-react-cart', entries)
        reference = '\n\n'.join(entries)
        for corrupted in [reference.replace('$10.90', '$999.99', 1),
                          reference.replace('Free shipping', '', 1),
                          reference.replace('or 9 x$1.21', '', 1)]:
            with self.subTest(corrupted=corrupted):
                hits = self.hits(corrupted, facts)
                self.assertFalse(hits['entry-1-name'])
                self.assertTrue(hits['entry-2-name'])

    def test_prefix_shipping_cannot_be_borrowed_from_next_card(self):
        facts = entry_facts('dynamic-react-cart', [
            'First\n$10\nFree shipping', 'Second\n$10\nFree shipping'])
        self.assert_complete('Free shipping First $10 Add to cart Free shipping Second $10 Add to cart', facts)
        hits = self.hits('First $10 Add to cart Free shipping Second $10 Add to cart', facts)
        self.assertFalse(hits['entry-1-shipping'])
        self.assertTrue(hits['entry-2-shipping'])

    def test_swagger_method_and_path_belong_to_summary(self):
        facts = entry_facts('dynamic-swagger', [
            'POST\n/pet/{petId}/uploadImage\nuploads an image',
            'GET\n/pet\nList pets', 'POST\n/user\nCreate user'])
        text = 'GET /pet/{petId}/uploadImage uploads an image GET /pet List pets POST /user Create user'
        hits = self.hits(text, facts)
        self.assertFalse(hits['entry-1-method'])
        self.assertTrue(hits['entry-2-method'])
        self.assertFalse(self.hits(text.replace('/user', '/user/login'), facts)['entry-3-path'])

    def test_overlapping_titles_do_not_invent_missing_record(self):
        facts = entry_facts('dynamic-react-cart', [
            'Blue T-Shirt\n$10', 'Marine Blue T-shirt\n$10'])
        hits = self.hits('Marine Blue T-shirt $10', facts)
        self.assertFalse(hits['entry-1-name'])
        self.assertTrue(hits['entry-2-name'])
        # A broken long-name record must still reserve its identity span.
        self.assertFalse(any(self.hits('Marine Blue T-shirt $99', facts).values()))

    def test_shuffled_and_identical_titles_with_distinct_descriptions(self):
        entries = ['$299\nSame title\nDark grey laptop\n9 reviews',
                   '$299\nSame title\nGold laptop\n9 reviews']
        facts = entry_facts('dynamic-catalog', entries)
        self.assert_complete('\n\n'.join(reversed(entries)), facts)
        hits = self.hits(entries[1], facts)
        self.assertFalse(hits['entry-1-description'])
        self.assertTrue(hits['entry-2-description'])

    def test_duplicate_identical_records_need_disjoint_tuples(self):
        facts = entry_facts('dynamic-product-reviews', ['2022-07-22\nGood', '2022-07-22\nGood'])
        hits = self.hits('2022-07-22 Good', facts)
        self.assertEqual(sum(bool(v) for k, v in hits.items() if k.endswith('-text')), 1)
        self.assert_complete('2022-07-22 Good 2022-07-22 Good', facts)

    def test_labels_separators_and_explicit_layouts(self):
        facts = entry_facts('dynamic-swagger', ['POST\n/pet\nCreate pet'])
        self.assert_complete('Method: POST | Path: /pet | Summary: Create pet', facts)
        facts = entry_facts('dynamic-catalog', ['$10\nTitle\nDescription\n1 reviews'])
        self.assert_complete('Title | Price: $10 | Description: Description | 1reviews', facts)
        facts = entry_facts('dynamic-quotes-api', ['“Hello.”\nby Someone\nTags: one two'])
        self.assert_complete('“Hello.”by Someone Tags: one two', facts)
        facts = entry_facts('dynamic-products-api', ['Hoodie\n$52'])
        self.assert_complete('Hoodie\\\\ \\ $52', facts)

    def test_arbitrary_windows_order_and_numeric_fragments_rejected(self):
        facts = entry_facts('dynamic-products-api', ['Shirt\n$10'])
        for text in ['Shirt unrelated product $10', 'Shirt $100', 'Shirt $10.90',
                     'Shirt -$10', 'Shirt $99 Other $10', '$10 Shirt']:
            self.assertFalse(any(self.hits(text, facts).values()), text)

    def test_fails_closed_without_mutating_frozen_facts(self):
        facts = [{'id': 'entry-1-unknown', 'alternatives': ['Hi']},
                 {'id': 'normal-fact', 'alternatives': ['Hi']}]
        before = copy.deepcopy(facts)
        self.assertEqual(self.hits('Hi', facts), {'entry-1-unknown': []})
        self.assertEqual(facts, before)
        facts = entry_facts('dynamic-products-api', ['Shirt\n$10'])
        self.assertFalse(any(self.hits('Shirt $10', facts + [facts[0]]).values()))

    def test_all_frozen_captures_and_missing_records(self):
        captures = []
        for filename in ('dynamic-browser-captures.json', 'dynamic-expansion-captures.json'):
            captures += json.loads((ROOT/'research'/filename).read_text())
        for capture in captures:
            facts = entry_facts(capture['id'], capture['entries'])
            with self.subTest(case=capture['id']):
                self.assert_complete(capture['rendered_text'], facts)
                self.assert_complete('\n\n'.join(reversed(capture['entries'])), facts)
                hits = self.hits('\n\n'.join(capture['entries'][1:]), facts)
                self.assertFalse(any(v for k, v in hits.items() if k.startswith('entry-1-')))

    def test_every_new_capture_field_corruption_is_rejected(self):
        captures = json.loads((ROOT/'research/dynamic-expansion-captures.json').read_text())
        for capture in captures:
            facts = entry_facts(capture['id'], capture['entries'])
            for fact in facts:
                index = int(fact['id'].split('-')[1]) - 1
                entries = capture['entries'][:]
                entries[index] = entries[index].replace(fact['alternatives'][0], 'WRONG', 1)
                with self.subTest(case=capture['id'], fact=fact['id']):
                    self.assertFalse(all(self.hits('\n\n'.join(entries), facts).values()))

    def test_retained_outputs_with_all_unbound_facts_present(self):
        """Exercise every retained dynamic output; no provider calls or writes.

        Compare with the injected normalization contract, not stale stored scores.
        Missing facts (including pending currency normalization) cannot pass here.
        """
        evidence=current_evidence_dir()
        if not (evidence/'results.jsonl').is_file():self.skipTest('requires persisted benchmark evidence')
        cases = {c['id']: c for c in json.loads((evidence/'dataset.json').read_text())['cases']
                 if c.get('track') == 'dynamic'}
        checked = 0
        for line in (evidence/'results.jsonl').read_text().splitlines():
            record = json.loads(line)
            if record['case_id'] not in cases or not record['ok']:
                continue
            facts = cases[record['case_id']]['required_facts']
            raw = (evidence/record['artifact_path']).read_text()
            folded = normalize_for_match(raw)
            if not all(any(fact_in_text(folded, normalize_for_match(a)) for a in f['alternatives']) for f in facts):
                continue
            checked += 1
            with self.subTest(attempt=record['attempt_id']):
                self.assert_complete(raw, facts)
        self.assertGreaterEqual(checked, 20)


if __name__ == '__main__':
    unittest.main()
