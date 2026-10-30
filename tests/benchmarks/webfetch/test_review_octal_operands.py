"""Consecutive octal expressions are code, not evidence of a line-number gutter."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


CASES = [
    ('octal-operands', '```c\n01 + 20;\n02 + 30;\n```', '01+20;02+30;', True),
    ('lost-octal-operands', '```c\n01 + 20;\n02 + 30;\n```', '+20;+30;', False),
    ('octal-operators', '```c\n01 - 20;\n02 * 30;\n```', '01-20;02*30;', True),
    ('lost-octal-operators', '```c\n01 - 20;\n02 * 30;\n```', '-20;*30;', False),
    ('numbered-octal-statement', '```c\n01 total = (\n02     010 + 020\n03 );\n```', 'total=(010+020);', True),
    ('altered-numbered-octal-statement', '```c\n01 total = (\n02     010 + 020\n03 );\n```', 'total=(+020);', False),
]


def _case(expected):
    return {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'literal-code', 'match_scope': 'code_compact', 'pattern': '^' + re.escape(expected) + '$'}]}


@pytest.mark.parametrize('identifier,text,expected,passed', CASES)
def test_code_operands_need_independent_statement_evidence_before_gutter_removal(identifier, text, expected, passed):
    result = evaluate_response(_case(expected), text.encode())
    assert result['passed'] is passed


def scoring_controls():
    for identifier, text, expected, passed in CASES:
        yield {'id': 'runtime8-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'numeric_operator_lines_are_not_independent_line_number_evidence',
               'case': _case(expected), 'artifact': text, 'ok': True, 'expected_task_pass': passed}
