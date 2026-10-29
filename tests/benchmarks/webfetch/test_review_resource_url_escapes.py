"""Only Markdown destination syntax consumes backslash punctuation escapes."""
import json

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


CANONICAL = 'https://example.test/file.png?q=a&b=c'
LITERAL = CANONICAL.replace('&', r'\&')


def controls():
    surfaces = [
        ('plain', LITERAL, {'answer_format': 'plain_text'}, LITERAL),
        ('html', '<a href="' + LITERAL + '">File</a>', {'answer_format': 'html'}, LITERAL),
        ('metadata', '', {'links': [{'url': LITERAL, 'title': 'File'}]}, LITERAL),
        ('json', json.dumps({'resources': [LITERAL]}), {}, LITERAL),
        ('bare-markdown', LITERAL, {'answer_format': 'markdown'}, LITERAL),
        ('markdown', '[File](' + LITERAL + ')', {'answer_format': 'markdown'}, CANONICAL),
        ('markdown-once', '[File](' + CANONICAL.replace('&', r'\\&') + ')',
         {'answer_format': 'markdown'}, LITERAL),
    ]
    root = ('Resource URL normalization must preserve literal query backslashes; '
            'Markdown destinations consume their own punctuation escapes exactly once.')
    for surface, artifact, metadata, destination in surfaces:
        for target, label in [(CANONICAL, 'unescaped'), (LITERAL, 'literal')]:
            expected = target == destination
            yield {
                'id': 'resource-query-backslash-' + surface + '-' + label,
                'category': 'format-normalization',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': root,
                'case': {'url': 'https://example.test/catalog', 'task_kind': 'resource',
                         'content_score_applicable': False,
                         'expected_resources': [{'urls': [target]}]},
                'artifact': artifact, 'metadata': metadata, 'ok': True,
                'expected_task_pass': expected,
                'expected_metrics': {'resource_candidate_urls': [destination]},
            }
    for extra in [False, True]:
        yield {
            'id': 'resource-query-backslash-target-only-' + ('extra' if extra else 'target'),
            'category': 'format-normalization',
            'failure_mode': 'false-positive' if extra else 'false-negative',
            'root_cause': root,
            'case': {'url': 'https://example.test/catalog', 'task_kind': 'resource',
                     'content_score_applicable': False,
                     'expected_resources': [{'urls': [CANONICAL]}],
                     'resource_output_contract': {'candidate_scope': 'image_answer_fields',
                                                  'unexpected': 'reject'}},
            'artifact': '', 'metadata': {'images': [CANONICAL, LITERAL] if extra else [CANONICAL]},
            'ok': True, 'expected_task_pass': not extra,
        }
    entity = CANONICAL.replace('&', '&amp;')
    escaped_entity = CANONICAL.replace('&', '&amp;amp;')
    syntax_controls = [
        ('entity-plain', entity, {'answer_format': 'plain_text'}, entity, CANONICAL),
        ('entity-metadata', '', {'links': [{'url': entity, 'title': 'File'}]}, entity, CANONICAL),
        ('entity-html-once', '<a href="' + escaped_entity + '">File</a>',
         {'answer_format': 'html'}, entity, CANONICAL),
        ('entity-markdown-once', '[File](' + escaped_entity + ')',
         {'answer_format': 'markdown'}, entity, CANONICAL),
    ]
    for encoded, other in [('%5C', '%255C'), ('%255C', '%5C')]:
        target = CANONICAL.replace('&', encoded + '&')
        distinct = CANONICAL.replace('&', other + '&')
        syntax_controls.append(('percent-' + encoded[1:], target,
                                {'answer_format': 'plain_text'}, target, distinct))
    for identifier, artifact, metadata, target, distinct in syntax_controls:
        for expected, resource in [(True, target), (False, distinct)]:
            yield {
                'id': 'resource-url-syntax-' + identifier + ('-same' if expected else '-distinct'),
                'category': 'format-normalization',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': ('Literal resource URLs preserve entity spellings and percent layers; '
                               'HTML and Markdown readers decode their source syntax once.'),
                'case': {'url': 'https://example.test/catalog', 'task_kind': 'resource',
                         'content_score_applicable': False,
                         'expected_resources': [{'urls': [resource]}]},
                'artifact': artifact, 'metadata': metadata, 'ok': True,
                'expected_task_pass': expected,
                'expected_metrics': {'resource_candidate_urls': [target]},
            }


CONTROLS = list(controls())


@pytest.mark.parametrize('row', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_resource_query_backslashes_keep_their_source_syntax(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=row['ok'],
                            metadata=row['metadata'])
    assert task_passed(row['ok'], metrics, row['case']['task_kind']) is row['expected_task_pass']
    for key, expected in row.get('expected_metrics', {}).items():
        assert metrics[key] == expected
