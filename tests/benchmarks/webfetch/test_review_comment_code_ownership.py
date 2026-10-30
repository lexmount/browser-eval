"""HTML comments and Markdown code claim backticks in source order."""
import pytest

from lexbench_webfetch.metrics import task_measured, task_passed
from lexbench_webfetch.body_coverage import body_requirements
from lexbench_webfetch.reference_quality import visible_mixed_html_text
from lexbench_webfetch.scoring import score_attempt


CASES = [
    ('comment-before-body', '<!-- ` -->\nRequired body ` here', True),
    ('hidden-comment', '<!-- Required body ` -->\nOther output ` here', False),
    ('inline-comment', 'Prefix <!-- ` --> Required body ` here', True),
    ('double-ticks', '<!-- `` -->\nRequired body `` here', True),
    ('code-before-comment', '`<!--` <!-- ` --> Required body ` here', True),
    ('actual-code-comment', '`<!-- Required body -->`', True),
    ('same-line-code-precedence', '`before <!-- Required body ` -->\nOther output', True),
    ('comment-block-interrupts', '`before\n<!-- Required body ` -->\nOther output', False),
    ('soft-line-code-precedence', '`before\ntext <!-- Required body ` -->', True),
    ('escaped-comment', r'\<!-- Required body ` payload` -->', True),
    ('fenced-comment-literal', '```text\n<!-- Required body ` -->\n```', True),
    ('indented-comment-literal', '    <!-- Required body ` -->', True),
    ('unclosed-comment', '<!-- `\nRequired body ` here', False),
    ('comment-owned-fence', '<!--\n```text\n-->\nRequired body', True),
    ('comment-hidden-fence', '<!--\n```text\nRequired body -->\nOther output', False),
    ('comment-owned-tilde', '<!--\n~~~text\n-->\nRequired body', True),
    ('comment-owned-indent', '<!--\n\n    Hidden note -->\nRequired body', True),
    ('comment-hidden-indent', '<!--\n\n    Required body -->\nOther output', False),
    ('comment-code-after', '<!-- ` -->\n`Required body`', True),
    ('comment-fence-after', '<!--\n```fake\n-->\n```text\nRequired body\n```', True),
    ('standalone-ticks-not-code', '`\n<!-- Required body -->\n`', False),
    ('valid-multiline-code', '`<!--\nRequired body\n-->`', True),
]


def case(body=False):
    requirements = ({'body_requirements': body_requirements('Required body')} if body else
                    {'required_facts': [{'id': 'fact', 'alternatives': ['Required body']}]})
    return {'url': 'https://example.test/', 'ground_truth_main_content': 'Required body',
            **requirements}


@pytest.mark.parametrize('identifier,source,expected', CASES)
@pytest.mark.parametrize('representation', [None, 'markdown'])
@pytest.mark.parametrize('body', [False, True])
def test_comment_and_code_ownership_preserves_visible_body(identifier, source, expected, representation, body):
    measured = score_attempt(case(body), source.encode(), ok=True,
                             metadata={'answer_format': representation} if representation else None)
    assert task_measured(measured)
    assert task_passed(True, measured, 'body') is expected
    assert measured['word_recall'] == (1.0 if expected else 0.0)


@pytest.mark.parametrize('source,expected', [
    ('<!-- ` -->\nRequired body ` here', '\nRequired body ` here'),
    ('`<!--` <!-- ` --> Required body ` here', '`<!--`  Required body ` here'),
    ('`before <!-- Required body ` -->', '`before <!-- Required body ` -->'),
    ('`before\n<!-- Required body ` -->\nOther output', '`before\n\nOther output'),
])
def test_shared_projection_does_not_mask_comment_terminators(source, expected):
    assert visible_mixed_html_text(source) == expected


def scoring_controls():
    selected = {'comment-before-body', 'hidden-comment', 'same-line-code-precedence', 'comment-block-interrupts',
                'comment-owned-fence', 'comment-hidden-fence', 'comment-owned-indent', 'comment-hidden-indent',
                'standalone-ticks-not-code', 'valid-multiline-code'}
    for identifier, source, expected in CASES:
        if identifier in selected:
            for body in ([False, True] if identifier in {'comment-before-body', 'hidden-comment', 'standalone-ticks-not-code', 'valid-multiline-code'} else [False]):
                yield {'id': 'comment27-' + identifier + ('-coverage' if body else '-fact'), 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Backticks already owned by HTML comments cannot open Markdown code; code opened before comment-like text retains its literal payload within the same inline block.',
                   'case': case(body), 'artifact': source, 'ok': True,
                   'metadata': {'answer_format': 'markdown'}, 'expected_task_pass': expected,
                   'expected_task_measured': True, 'expected_metrics': {'word_recall': 1.0 if expected else 0.0}}
