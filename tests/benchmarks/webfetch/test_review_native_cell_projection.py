"""Native table text is decoded once before scalar validation."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def cases():
    for identifier, cell, expected_value, expected in [
        ('escaped-literal', '&lt;value&gt;', '<value>', True),
        ('empty-markup', '<value></value>', '<value>', False),
        ('native-formatting', '<strong>Ready</strong>', 'Ready', True),
        ('escaped-formatting-distinct', '&lt;strong&gt;Ready&lt;/strong&gt;', 'Ready', False),
    ]:
        group = {'id': 'items', 'kind': 'table', 'minimum_records': 1, 'identity_column': 'id',
                 'required_columns': [{'id': 'id', 'kind': 'integer', 'aliases': ['ID']},
                    {'id': 'value', 'kind': 'token', 'aliases': ['Value'], 'pattern': re.escape(expected_value)}]}
        text = '<table><tr><th>ID</th><th>Value</th></tr><tr><td>1</td><td>' + cell + '</td></tr></table>'
        yield identifier, text, {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [group]}}, expected


@pytest.mark.parametrize('identifier,text,case,expected', list(cases()))
@pytest.mark.parametrize('representation', [None, 'html'])
def test_native_cells_keep_rendered_text_without_reinterpreting_tags(identifier, text, case, expected, representation):
    result = evaluate_response(case, text.encode(), metadata={'answer_format': representation} if representation else None)
    assert result['measured']
    assert result['passed'] is expected


def scoring_controls():
    for identifier, text, case, expected in cases():
        yield {'id': 'native-cell-projection-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Table DOM projection owns decoding; scalar validation must retain displayed literal tags and distinguish actual formatting from escaped text.',
               'case': case, 'artifact': text, 'metadata': {'answer_format': 'html'},
               'ok': True, 'expected_task_pass': expected}
