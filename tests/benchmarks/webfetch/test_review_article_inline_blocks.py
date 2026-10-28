"""Inline literals stay in one Markdown block; title exclusions apply to mixed HTML."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response

from .test_review_component_representation import POST, COMMENT
from .test_review_migration_articles import POSTS


def cases():
    title = {'version': 1, 'alternatives': ['Observed title'], 'html_selector': 'h1'}
    for name, text, expected in (
        ('blank-paragraph', 'before `\n\n# Observed title\n\nafter `', True),
        ('atx-boundary', 'before `\n# Observed title\nafter `', True),
        ('soft-line', 'before `\nObserved title\nafter `', False),
        ('soft-closing-line', 'before `Observed title\n`', False),
    ):
        yield ('title-inline-block-' + name, text, {'title_contract': title}, expected)
    for component, contract, text in (
        ('post', POST, 'before `\n\n## Alice [Today](/p/1)\n\nReal post text.\n\nafter `'),
        ('comment', COMMENT, '## Comments\n\nbefore `\n\n### Alice\nReal comment text.\n\nafter `'),
    ):
        yield (component + '-inline-block-real-heading', text, {component + '_contract': contract}, True)
    for mixed in (False, True):
        for excluded in (False, True):
            contract = {**title, 'excluded_html_selectors': ['h1.screen-reader-only']}
            content = ('# Other title\n\n' if mixed else '') + '<h1 class="' + ('screen-reader-only' if excluded else 'visible') + '">Observed title</h1>'
            yield (f'title-selector-{"mixed" if mixed else "html"}-{"excluded" if excluded else "visible"}',
                   content, {'title_contract': contract}, not excluded)
    literal = '<h1 class="screen-reader-only">Observed title</h1>'
    yield ('title-selector-code-literal', '# `' + literal + '`',
           {'title_contract': {**title, 'alternatives': [literal], 'excluded_html_selectors': ['h1.screen-reader-only']}}, True)
    for body, expected in [('', False), ('Actual code', True)]:
        text = '## Alice [Today](/post/1)\n\n> Bob\n> `' + body + '\n> `'
        yield ('quote-soft-line-code-' + ('visible' if expected else 'empty'), text,
               {'post_contract': POSTS}, expected)


@pytest.mark.parametrize('identifier,text,components,expected', list(cases()))
def test_inline_blocks_and_mixed_title_exclusions(identifier, text, components, expected):
    result = evaluate_response({'url': 'https://example.test/', 'source_benchmark': components}, text.encode(),
                               metadata={'answer_format': 'markdown'})
    assert result['measured']
    assert result['passed'] is expected


@pytest.mark.parametrize('text,count', [
    ('before `\n\n# Heading\n\nafter `', 0),
    ('before `\n# Heading\nafter `', 0),
    ('before `\nsoft continuation\nafter `', 1),
    ('before `soft continuation\n`', 1),
    ('> before `\n> soft continuation `', 1),
    ('> > before `\n> soft continuation `', 1),
    ('before `\n2. Not an interrupting list\nafter `', 1),
    ('before `\n1. A new list\nafter `', 0),
    ('before `\n<div>\nafter `', 0),
])
def test_shared_inline_owner_respects_blocks_and_soft_lines(text, count):
    from lexbench_webfetch.reference_quality import markdown_inline_code_spans
    assert len(markdown_inline_code_spans(text)) == count


def scoring_controls():
    selected = {'title-inline-block-blank-paragraph', 'title-inline-block-atx-boundary',
                'title-inline-block-soft-line', 'title-selector-mixed-excluded',
                'title-selector-mixed-visible', 'title-selector-code-literal'}
    for identifier, text, components, expected in cases():
        if identifier in selected:
            yield {'id': identifier, 'failure_mode': 'false-negative' if expected else 'false-positive',
                   'category': 'answer-surface',
                   'root_cause': 'Markdown code spans cannot cross blocks, and excluded HTML title nodes must stay excluded in mixed content.',
                   'case': {'url': 'https://example.test/', 'source_benchmark': components},
                   'artifact': text, 'metadata': {'answer_format': 'markdown'},
                   'ok': True, 'expected_task_pass': expected}
