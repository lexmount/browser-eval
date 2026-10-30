"""Container-owned fences and quoted attributes retain their rendered ownership."""
import copy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import markdown_fenced_code_blocks, visible_mixed_html_text
from lexbench_webfetch.scoring import score_attempt


IMAGE = '![Example](/target.png)'
FENCES = [
    ('quote', '> ~~~\n> ' + IMAGE + '\n> ~~~', False),
    ('nested-quote', '> > ```text\n> > ' + IMAGE + '\n> > ```', False),
    ('list', '- ~~~\n  ' + IMAGE + '\n  ~~~', False),
    ('ordered-list', '1. ```text\n   ' + IMAGE + '\n   ```', False),
    ('list-continuation', '- Parent\n  ~~~\n  ' + IMAGE + '\n  ~~~', False),
    ('nested-list', '- Parent\n  - ~~~\n    ' + IMAGE + '\n    ~~~', False),
    ('quote-list', '> - ~~~\n>   ' + IMAGE + '\n>   ~~~', False),
    ('list-quote', '- > ~~~\n  > ' + IMAGE + '\n  > ~~~', False),
    ('quote-html', '> ~~~html\n> <img src="/target.png">\n> ~~~', False),
    ('quote-ended-blank', '> ~~~\n> example\n\n' + IMAGE, True),
    ('quote-ended-text', '> ~~~\n> example\n' + IMAGE, True),
    ('nested-quote-ended', '> > ~~~\n> > example\n>\n> ' + IMAGE, True),
    ('list-ended', '- ~~~\n  example\n\n' + IMAGE, True),
    ('quote-continued', '> ~~~\n> example\n>\n> ' + IMAGE + '\n> ~~~', False),
    ('list-continued', '- ~~~\n  example\n\n  ' + IMAGE + '\n  ~~~', False),
    ('heading-before-list', '# Heading\n2. ~~~\n   ' + IMAGE + '\n   ~~~', False),
    ('thematic-before-root', '- - -\n  ~~~\n' + IMAGE + '\n  ~~~', False),
    ('paragraph-not-ordered-list', 'Paragraph\n2. ~~~\n' + IMAGE + '\n~~~', True),
]
BODY = [
    ('comment-attribute', '<span title="<!--">Required fact</span>', True),
    ('quoted-greater', "<span title='x > <!--'>Required fact</span>", True),
    ('closed-attribute-comment', '<span title="<!-- noise -->">Required fact</span>', True),
    ('attribute-only', '<span title="<!-- Required fact -->">Other</span>', False),
    ('real-comment', '<span title="<!--">Other</span><!-- Required fact -->', False),
    ('code-before-comment', '`<span title="` <!-- Required fact -->', False),
    ('code-inside-attribute-owner', '<span title="<!--">`<!-- Required fact -->`</span>', True),
    ('escaped-opener', r'\<!-- Required fact -->', True),
    ('quote-code-comment', '> ~~~text\n> <!-- Required fact -->\n> ~~~', True),
    ('comment-after-code', '> ~~~text\n> Other\n> ~~~\n<!-- Required fact -->', False),
]


def public_controls():
    for representation in [None, 'markdown']:
        for name, source, expected in FENCES:
            yield control('fence-' + name, source, expected, representation, resource=True)
        for name, source, expected in BODY:
            yield control('body-' + name, source, expected, representation, resource=False)
    yield control('html-fence-text', '<blockquote>~~~<img src="/target.png">~~~</blockquote>',
                  True, 'html', resource=True)
    yield control('plain-comment-text', '<!-- Required fact -->', True, 'plain_text', resource=False)


def control(identifier, source, expected, representation, *, resource):
    case = {'url': 'https://example.test/page'}
    if resource:
        case.update(task_kind='resource', content_score_applicable=False,
                    expected_resources=[{'urls': ['/target.png']}])
    else:
        case['required_facts'] = [{'id': 'fact', 'alternatives': ['Required fact']}]
    return {
        'id': 'markdown30-' + identifier + '-' + (representation or 'inferred'),
        'category': 'answer-surface',
        'failure_mode': 'false-negative' if expected else 'false-positive',
        'root_cause': ('Shared Markdown source ownership keeps fenced examples inside quote/list '
                       'containers literal and distinguishes actual comments from quoted attributes.'),
        'case': copy.deepcopy(case), 'artifact': source, 'ok': True,
        'metadata': {'answer_format': representation} if representation else {},
        'expected_task_pass': expected,
    }


CONTROLS = list(public_controls())


@pytest.mark.parametrize('row', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_markdown_body_and_resource_ownership_through_public_scorer(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(True, metrics, row['case'].get('task_kind', 'body')) is row['expected_task_pass']


def test_comment_removal_keeps_adjacent_text_adjacent():
    assert visible_mixed_html_text('joined<!-- hidden -->word') == 'joinedword'


@pytest.mark.parametrize('source', [FENCES[0][1], FENCES[2][1], FENCES[6][1], FENCES[7][1]])
def test_shared_fence_payload_is_independent_of_its_container(source):
    blocks, comments = markdown_fenced_code_blocks(source)
    assert comments == []
    assert [payload for _, _, payload in blocks] == [IMAGE + '\n']
