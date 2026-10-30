"""HTML attributes cannot open Markdown comments or consume later resources."""
import copy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import markdown_comment_spans, markdown_literal_spans
from lexbench_webfetch.scoring import score_attempt


TARGET = 'https://example.test/target.png'
NOISE = 'https://example.test/noise.png'
CASES = [
    ('double-attribute', '<span title="<!--">' + TARGET + '</span>', True),
    ('single-attribute', "<span title='<!--'>" + TARGET + '</span>', True),
    ('quoted-greater', '<span title="x > <!--">' + TARGET + '</span>', True),
    ('closed-attribute-comment', '<span title="<!-- noise -->">' + TARGET + '</span>', True),
    ('attribute-backtick', '<span title="` <!--">' + TARGET + ' ` tail</span>', True),
    ('multiline-attribute-fence', '<span title="\n<!--\n```\n">' + TARGET + '</span>', True),
    ('real-hidden-target', '<span title="<!--">Other</span><!-- ' + TARGET + ' -->', False),
    ('real-hidden-noise', '<span title="<!--">' + TARGET + '</span><!-- ' + NOISE + ' -->', True),
    ('attribute-only', '<span title="<!-- ' + TARGET + ' -->">Other</span>', False),
    ('relative-visible', '<span title="<!--"><manual.pdf></span>', True),
    ('relative-hidden', '<span title="<!--">Other</span><!-- <manual.pdf> -->', False),
    ('escaped-opener', r'\<!-- ' + TARGET + ' -->', True),
    ('code-owned-resource', '<span title="<!--">`[Target](' + TARGET + ')`</span>', False),
    ('code-before-real-comment', '`<span title="` <!-- ' + TARGET + ' -->', False),
    ('fence-before-real-comment', '```\n<span title="\n```\n<!-- ' + TARGET + ' -->', False),
    ('literal-before-attribute', '`<!--` <span title="<!--">' + TARGET + '</span>', True),
    ('real-comment-owned-ticks', '<!-- <span title="`"> ' + TARGET + ' ` -->', False),
]


def public_controls():
    for name, source, expected in CASES:
        case = {'url': 'https://example.test/page', 'task_kind': 'resource',
                'content_score_applicable': False,
                'expected_resources': [{'urls': ['/manual.pdf' if name.startswith('relative-') else TARGET]}]}
        if name == 'real-hidden-noise':
            case['resource_output_contract'] = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
        for representation in [None, 'markdown']:
            yield {
                'id': 'comment29-attribute-' + name + '-' + (representation or 'inferred'),
                'category': 'answer-surface',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': ('HTML tag attributes own their quoted comment and fence spellings; '
                               'real comments and earlier literal code retain their source-order ownership.'),
                'case': copy.deepcopy(case), 'artifact': source, 'ok': True,
                'metadata': {'answer_format': representation} if representation else {},
                'expected_task_pass': expected,
            }


CONTROLS = list(public_controls())


@pytest.mark.parametrize('control', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_comment_attribute_ownership_through_public_scorer(control):
    metrics = score_attempt(control['case'], control['artifact'].encode(), ok=True,
                            metadata=control['metadata'])
    assert task_passed(True, metrics, 'resource') is control['expected_task_pass']


@pytest.mark.parametrize('name,source,expected', CASES[:6])
def test_attribute_payload_does_not_open_comments_or_code(name, source, expected):
    assert markdown_comment_spans(source) == []
    assert markdown_literal_spans(source) == []
