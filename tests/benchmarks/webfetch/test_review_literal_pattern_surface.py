"""Page patterns see rendered code text without reparsing its literal payload."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


CASES = [
    ('plain', 'Required label', '^Required label$', True),
    ('inline', '`Required label`', '^Required label$', True),
    ('double-inline', '``Required label``', '^Required label$', True),
    ('triple-inline', '```Required label```', '^Required label$', True),
    ('payload-backtick', '``Required ` label``', '^Required ` label$', True),
    ('inline-space', '` Required label `', '^Required label$', True),
    ('multiline-inline', '`Required\nlabel`', '^Required label$', True),
    ('fenced', '```markdown\nRequired label\n```', '^Required label$', True),
    ('tilde-fenced', '~~~markdown\nRequired label\n~~~', '^Required label$', True),
    ('wrong-label', '`Other label`', '^Required label$', False),
    ('fence-language', '```markdown\nRequired label\n```', '^markdown$', False),
    ('literal-image', '`![diagram](example.png)`', r'^!\[diagram\]\(example\.png\)$', True),
    ('actual-image', '![diagram](example.png)', r'^!\[diagram\]\(example\.png\)$', False),
]


def contract(pattern):
    return {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_role': 'required', 'stable_text_anchors': [], 'page_patterns': [pattern]}


@pytest.mark.parametrize('identifier,text,pattern,expected', CASES)
def test_page_patterns_remove_code_formatting_after_literal_visibility(identifier, text, pattern, expected):
    acceptance = contract(pattern)
    assert evaluate_stable_task(text, acceptance)['stable_task_pass'] is expected
    result = evaluate_response({'url': 'https://example.test/', 'acceptance_contract': acceptance}, text.encode())
    assert result['passed'] is expected


def scoring_controls():
    for identifier, text, pattern, expected in CASES:
        yield {'id': 'literal-surface-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Code syntax remains protected during visibility normalization, then only its rendered payload supplies the final page-pattern surface.',
               'case': {'url': 'https://example.test/', 'acceptance_contract': contract(pattern)},
               'artifact': text, 'ok': True, 'expected_task_pass': expected}
