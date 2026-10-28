"""Field operands remain literal; document literals require visible evidence."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def field_case(kind, expected):
    spec = {'id': 'status', 'kind': 'token', 'aliases': ['Status'], 'pattern': re.escape(expected)}
    group = {'id': 'values', 'kind': kind, 'required_fields': [spec]}
    if kind == 'named_link':
        group.update(hosts=['example.test'], entry_path=r'/item/1', minimum_records=1)
    else:
        group['fixed_values'] = {'status': expected}
    return {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [group]}}


def literal_case(expected='Alpha'):
    return {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [
        {'id': 'visible', 'kind': 'literals', 'required_literals': [expected]}]}}


def cases():
    for kind in ['named_link', 'field_report']:
        prefix = 'Example\nhttps://example.test/item/1\n' if kind == 'named_link' else ''
        for label, value in [('bold', '**Ready**'), ('code', '`Ready`'), ('link', '[Ready](/status)'),
                             ('html', '<b>Ready</b>'), ('pipe', '|Ready|'), ('encoded', 'Ready%20Now'),
                             ('camel', 'ReadyNow')]:
            for matching in [True, False]:
                expected = value if matching else 'Ready'
                yield (f'field-{kind}-{label}-' + ('exact' if matching else 'distinct'),
                       prefix + 'Status:' + value, field_case(kind, expected), 'plain_text', matching)
    for identifier, text, representation, expected in [
        ('destination-only', '[unrelated](https://example.test/Alpha)', 'markdown', False),
        ('visible-label', '[Alpha](https://example.test/unrelated)', 'markdown', True),
        ('encoded-url', 'https://example.test/%41lpha', 'markdown', False),
        ('plain-url-encoding', 'https://example.test/%41lpha', 'plain_text', False),
        ('plain-literal-markup', '[unrelated](https://example.test/Alpha)', 'plain_text', True),
        ('html-attribute', '<a href="https://example.test/Alpha">unrelated</a>', 'html', False),
        ('html-visible', '<p>Alpha</p>', 'html', True),
        ('html-literal-markdown', '<p>[unrelated](https://example.test/Alpha)</p>', 'html', True),
        ('inline-code', '`[unrelated](https://example.test/Alpha)`', 'markdown', True),
        ('fenced-code', '```text\n[unrelated](https://example.test/Alpha)\n```', 'markdown', True),
        ('image-alt', '![Alpha](image.png)', 'markdown', False),
    ]:
        yield 'literal-' + identifier, text, literal_case(), representation, expected
    for representation, expected in [('plain_text', True), ('markdown', False), ('html', True)]:
        text = '<p>**Ready**</p>' if representation == 'html' else '**Ready**'
        yield 'literal-formatting-' + representation, text, literal_case('**Ready**'), representation, expected
    yield 'literal-formatting-auto-html', '<p>**Ready**</p>', literal_case('**Ready**'), None, True
    for representation in ['html', None]:
        yield 'field-html-literal-' + str(representation), '<p>Status:**Ready**</p>', field_case('field_report', '**Ready**'), representation, True
        yield 'field-html-distinct-' + str(representation), '<p>Status:**Ready**</p>', field_case('field_report', 'Ready'), representation, False
    for kind in ['named_link', 'field_report']:
        text = ('[Example](/item/1)\n' if kind == 'named_link' else '') + 'Status:**Ready**'
        for expected, passed in [('Ready', True), ('**Ready**', False)]:
            yield 'field-markdown-' + kind + expected, text, field_case(kind, expected), 'markdown', passed


@pytest.mark.parametrize('identifier,text,case,representation,expected', list(cases()))
def test_field_values_and_visible_literal_evidence(identifier, text, case, representation, expected):
    result = evaluate_response(case, text.encode(), metadata={'answer_format': representation} if representation else None)
    assert result['measured']
    assert result['passed'] is expected


def scoring_controls():
    selected = {'field-named_link-bold-exact', 'field-named_link-bold-distinct',
                'field-field_report-pipe-exact', 'field-field_report-pipe-distinct',
                'literal-destination-only', 'literal-visible-label', 'literal-encoded-url',
                'literal-inline-code', 'literal-plain-literal-markup', 'literal-html-attribute',
                'field-html-literal-None', 'field-html-distinct-None'}
    for identifier, text, case, representation, expected in cases():
        if identifier in selected:
            yield {'id': 'composite-visible-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Composite fields preserve declared literal values; required literals match displayed text rather than link destinations or decoded URI bytes.',
                   'case': case, 'artifact': text, 'metadata': {'answer_format': representation} if representation else None,
                   'ok': True, 'expected_task_pass': expected}
