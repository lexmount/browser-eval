"""Inferred native cards retain layout; declared Markdown retains literal blocks."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.reference_quality import normalize_native_markdown_layout, markdown_literal_spans


DATE = '\t4 hours ago ## [First item](/item/1)\nNeutral\nFirst source\n'
IMAGE = ('        - [![Generic device](device.png)](/item/1)\n'
         '          [Generic device](/item/1)\n\nA clear device description.')
DATE_CASE = {'url': 'https://example.test/', 'composite_acceptance': {
    'version': 1, 'groups': [{'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
        'hosts': ['example.test'], 'entry_path': r'/item/[0-9]+', 'record_line_pattern': r'^##\s+',
        'required_fields': [{'id': 'time', 'kind': 'date_or_time'},
                            {'id': 'label', 'kind': 'text', 'pattern': 'Neutral'},
                            {'id': 'source', 'kind': 'text', 'minimum_chars': 2}]}]}}
IMAGE_CASE = {'url': 'https://example.test/', 'required_facts': [
    {'id': 'entry-1-name', 'alternatives': ['Generic device']},
    {'id': 'entry-1-description', 'alternatives': ['A clear device description.']}]}


def cases():
    for identifier, text, case in [('date', DATE, DATE_CASE), ('image', IMAGE, IMAGE_CASE)]:
        yield identifier + '-auto', text, case, None, True
        yield identifier + '-declared-markdown', text, case, 'markdown', False
        body_indented = ('    4 hours ago ## [First item](/item/1)\n    Neutral\n    First source\n'
                         if identifier == 'date' else IMAGE.replace('A clear device description.', '    A clear device description.'))
        yield identifier + '-fully-indented', body_indented, case, None, False
        yield identifier + '-fenced', '```markdown\n' + text + '\n```', case, None, False


@pytest.mark.parametrize('identifier,text,case,representation,expected', list(cases()))
def test_native_layout_inference_is_separate_from_explicit_markdown(identifier, text, case, representation, expected):
    metadata = {'answer_format': representation} if representation else None
    result = evaluate_response(case, text.encode(), metadata=metadata)
    assert result['measured']
    assert result['passed'] is expected


@pytest.mark.parametrize('text', [DATE, IMAGE])
def test_inferred_layout_is_idempotent_and_does_not_change_strict_lexer(text):
    normalized = normalize_native_markdown_layout(text)
    assert normalized != text
    assert normalize_native_markdown_layout(normalized) == normalized
    assert markdown_literal_spans(text)
    assert not markdown_literal_spans(normalized)


@pytest.mark.parametrize('text', [
    '    [Example](/item/1)\nOutside prose.',
    IMAGE.replace('[Generic device](/item/1)\n', '[Generic device](/item/2)\n'),
    IMAGE.replace('A clear device description.', '    A clear device description.'),
    '```markdown\n' + DATE + '```',
    '    not a date ## [Example](/item/1)\nOutside prose.',
    '<article><pre>\n' + DATE + '</pre></article>',
])
def test_inference_keeps_unqualified_examples_literal(text):
    assert normalize_native_markdown_layout(text) == text


def scoring_controls():
    for identifier, text, case, representation, expected in cases():
        yield {'id': 'native-card-layout-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Native serializer card indentation is inferred only for undeclared answers; explicit Markdown and complete code examples retain literal ownership.',
               'case': case, 'artifact': text, 'ok': True,
               'metadata': {'answer_format': representation} if representation else None,
               'expected_task_pass': expected}
