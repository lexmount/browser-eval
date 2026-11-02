"""Comment timestamps must not become colon-delimited authors."""
from copy import deepcopy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt

from .test_review_migration_articles import COMMENTS, public_case


def controls():
    for boundary in ('### Bob', '[Bob](https://example.test/bob)', '**Bob**'):
        contract = deepcopy(COMMENTS)
        contract['minimum_comments'] = 2
        yield {
            'id': 'comment-speech-then-structural-' + ('heading' if boundary.startswith('#') else 'link' if boundary.startswith('[') else 'bold'),
            'failure_mode': 'false-negative', 'category': 'structured-extraction',
            'root_cause': 'Speech-attribution filtering suppressed independent structural author boundaries.',
            'case': public_case('comment_contract', contract),
            'artifact': 'Benchmark page\n\n## Comments\n\nAlice 说：\n\nFirst reply.\n\n' + boundary + '\n\nSecond reply.',
            'ok': True, 'metadata': {'answer_format': 'markdown'},
            'expected_task_pass': True, 'expected_metrics': {'article_comment_count': 2},
        }
    for surface in ('markdown', 'plain_text'):
        for stamp in ('2024年9月12日 15:42', '2024-09-12 15:42', '15:42'):
            for footer in ('', ' | # | 引用'):
                for second in (False, True):
                    contract = deepcopy(COMMENTS)
                    contract['minimum_comments'] = 2
                    heading = '## Comments' if surface == 'markdown' else 'Comments'
                    content = 'Benchmark page\n\n' + heading + '\n\nAlice: First reply.\n\n' + stamp + footer
                    if second:
                        content += '\n\nBob: The event was on 2024年9月12日 15:42.'
                    yield {
                        'id': f'comment-timestamp-owner-{surface}-{stamp}-{bool(footer)}-{second}',
                        'failure_mode': 'false-negative' if second else 'false-positive',
                        'category': 'structured-extraction',
                        'root_cause': 'Timestamp metadata was parsed as a colon-delimited author before metadata filtering.',
                        'case': public_case('comment_contract', contract),
                        'artifact': content, 'ok': True,
                        'metadata': {'answer_format': surface},
                        'expected_task_pass': second,
                        'expected_metrics': {'article_comment_count': 2 if second else 1},
                    }
    for second in (False, True):
        contract = deepcopy(COMMENTS)
        contract['minimum_comments'] = 2
        content = 'Benchmark page\n\n## Comments\n\nAlice:\n\nFirst reply.\n\nCSS: display flex.'
        if second:
            content += '\n\nBob:\n\nSecond reply.'
        yield {
            'id': f'comment-standalone-author-prose-colon-{second}',
            'failure_mode': 'false-negative' if second else 'false-positive',
            'category': 'structured-extraction',
            'root_cause': 'Inline prose colon paragraphs were counted as authors within a standalone-author comment format.',
            'case': public_case('comment_contract', contract),
            'artifact': content, 'ok': True, 'metadata': {'answer_format': 'markdown'},
            'expected_task_pass': second,
            'expected_metrics': {'article_comment_count': 2 if second else 1},
        }
        content = 'Benchmark page\n\n## Comments\n\nAlice 说：\n\nFirst reply.\n\nCSS:\n\ndisplay flex.\n\n> @Bob:\n> quoted reply.'
        if second:
            content += '\n\nBob 说：\n\nSecond reply.'
        yield {
            'id': f'comment-explicit-speech-author-{second}',
            'failure_mode': 'false-negative' if second else 'false-positive',
            'category': 'structured-extraction',
            'root_cause': 'Colon-ended explanations and quoted addressees broke explicit standalone speech-attribution ownership.',
            'case': public_case('comment_contract', contract),
            'artifact': content, 'ok': True, 'metadata': {'answer_format': 'markdown'},
            'expected_task_pass': second,
            'expected_metrics': {'article_comment_count': 2 if second else 1},
        }


@pytest.mark.parametrize('row', list(controls()), ids=lambda row: row['id'])
def test_timestamp_does_not_supply_an_additional_comment(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert metrics['article_comment_count'] == row['expected_metrics']['article_comment_count']
    assert task_passed(row['case'], metrics) is row['expected_task_pass']
