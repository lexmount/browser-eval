import copy
import json
import re
import unittest
from pathlib import Path
from lexbench_webfetch.metrics import evaluate_content

ROOT=Path(__file__).resolve().parents[1]


def _entry_facts(case_id, entries):
    fields = (['quote', 'author', 'tags'] if case_id in {'dynamic-quotes-api','dynamic-delayed'}
              else ['name','price'] if case_id=='dynamic-products-api'
              else ['price','title','description','reviews'] if case_id=='dynamic-catalog'
              else ['date','text'] if case_id in {'dynamic-reviews-graphql','dynamic-product-reviews'}
              else ['name','description'] if case_id=='dynamic-angular-phones'
              else ['method','path','summary'] if case_id=='dynamic-swagger'
              else ['family'] if case_id=='dynamic-iframe'
              else ['name','price','financing','shipping'] if case_id=='dynamic-react-cart' else None)
    if not fields:raise ValueError('Unknown dynamic case')
    facts=[]
    for i,entry in enumerate(entries,1):
        values=[line.strip() for line in entry.splitlines() if line.strip()]
        entry_fields = fields
        if case_id == 'dynamic-react-cart':
            # Shipping and financing are optional visible fields on these cards.
            entry_fields = ['name', 'price']
            if len(values)>2 and values[2].startswith('or '):entry_fields.append('financing')
            if values[-1]=='Free shipping':entry_fields.append('shipping')
        if len(values)!=len(entry_fields):raise ValueError(f'Incomplete or unexpected fields: {case_id} entry {i}')
        for field,value in zip(entry_fields,values):
            if field=='author':value=value.removeprefix('by ')
            if field=='tags':value=value.removeprefix('Tags: ')
            alternatives=[value]
            # Numeric count + its label can be adjacent in Markdown extraction.
            # Keep the whole number/unit, never remove arbitrary word boundaries.
            if field=='reviews' and re.fullmatch(r'\d+\s+reviews?',value):
                alternatives.append(re.sub(r'\s+','',value))
            facts.append({'id':f'entry-{i}-{field}','alternatives':alternatives})
    return facts

class DynamicReferenceTests(unittest.TestCase):
    def test_all_observed_fields_are_required(self):
        captures=json.loads((ROOT/'research/dynamic-browser-captures.json').read_text())
        captures+=json.loads((ROOT/'research/dynamic-expansion-captures.json').read_text())
        for c in captures:
            facts=_entry_facts(c['id'],c['entries'])
            ref=c['rendered_text']
            score=lambda text:evaluate_content(text,[],required_facts=facts,min_content_chars=1)
            self.assertTrue(score(ref)['content_pass'],c['id'])
            # Delete each occurrence: duplicated fields cannot mask missing field coverage.
            for fact in facts:
                value=fact['alternatives'][0]
                self.assertFalse(score(ref.casefold().replace(value.casefold(),''))['content_pass'],(c['id'],fact['id']))

    def test_placeholder_or_incomplete_card_is_rejected(self):
        for entries in [[''],['Only a product name']]:
            with self.assertRaises(ValueError):_entry_facts('dynamic-products-api',entries)

    def test_count_label_spacing_preserves_numeric_identity(self):
        facts=_entry_facts('dynamic-catalog',['$10\nTitle\nDescription\n1 reviews'])
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
        facts = _entry_facts(case['id'], updated)
        fresh = '\n'.join(updated)
        stale = '\n'.join(case['entries'])
        score = lambda text: evaluate_content(text, [], required_facts=facts, min_content_chars=1)
        self.assertTrue(score(fresh)['content_pass'])
        self.assertFalse(score(stale)['content_pass'])
        self.assertNotEqual(old, updated[0])
