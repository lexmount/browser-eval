"""Scoring-only header equivalence; synthetic answers, no provider calls."""
from copy import deepcopy
import unittest

from lexbench_webfetch.structured_tables import parse_table_records, validate_table


class TableHeaderEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.contract = {
            'table_columns': [
                {'name': 'name', 'label': 'Name', 'type': 'text'},
                {'name': 'amount', 'label': 'Amount', 'type': 'integer'},
            ],
            'record_identity_fields': ['name'],
            'table_scope': {
                'row_selector': 'table tbody tr', 'cell_selector': 'td',
                'start_pattern': r'^Name\s*$', 'end_pattern': r'^Footer$',
                'plain_row_pattern': r'(?P<name>[^\t]+)\t(?P<amount>\d+)',
            },
        }
        validate_table(self.contract)
        self.table = '| Name | Amount |\n| --- | --- |\n| Alpha | 7 |\n| Beta | 8 |'

    def parse(self, text, contract=None):
        return parse_table_records(text, text, contract or self.contract)

    def test_plain_html_and_markdown_preserve_same_fields(self):
        expected = self.parse('Name\nAlpha\t7\nBeta\t8\nFooter')
        self.assertEqual(len(expected), 2)
        self.assertTrue(all(r['valid'] for r in expected))
        for text in (self.table, self.table.replace('\n', '\r\n'),
                     'Intro\n' + self.table + '\nFooter\nGamma\t9',
                     '<table><tbody><tr><td>Alpha</td><td>7</td></tr>'
                     '<tr><td>Beta</td><td>8</td></tr></tbody></table>'):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), expected)

    def test_missing_or_invalid_cells_remain_invalid(self):
        for changed in ('Beta |', 'Beta | nope'):
            rows = self.parse(self.table.replace('Beta | 8', changed))
            self.assertFalse(all(r['valid'] for r in rows))
        self.assertEqual(self.parse('| Name |\n| --- |\n| Alpha |'), [])

    def test_scope_heading_cannot_be_bypassed_by_column_names(self):
        changed = deepcopy(self.contract)
        changed['table_scope']['start_pattern'] = '^Target section$'
        self.assertEqual(self.parse(self.table, changed), [])
        self.assertEqual(self.parse('Name\nFooter\n' + self.table), [])

    def test_non_header_and_code_examples_are_not_answers(self):
        self.assertEqual(self.parse(self.table.replace('---', '...')), [])
        self.assertEqual(self.parse('Example:\n```markdown\n' + self.table + '\n```\nNo data'), [])
        self.assertEqual(self.parse('| Wrong | Amount |\n| --- | --- |\n| Name | 7 |'), [])

    def test_non_latin_header_uses_same_rule(self):
        changed = deepcopy(self.contract)
        changed['table_columns'][0]['label'] = 'الشخص'
        changed['table_scope']['start_pattern'] = r'^الشخص\s*$'
        self.assertEqual(self.parse(self.table.replace('Name', 'الشخص'), changed), self.parse(self.table))
