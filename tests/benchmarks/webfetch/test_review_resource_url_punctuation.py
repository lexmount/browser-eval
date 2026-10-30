"""Parsed URI punctuation remains resource data rather than source wrappers."""
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


PUNCTUATION = [
    ('greater', '/item>', '/item%3E', '/item'),
    ('less', '/item<', '/item%3C', '/item'),
    ('quotes', '/"item"', '/%22item%22', '/item'),
    ('apostrophe', "/item'", "/item'", '/item'),
    ('query-greater', '/file?q=item>', '/file?q=item%3E', '/file?q=item'),
    ('query-quotes', '/file?q="item"', '/file?q=%22item%22', '/file?q=item'),
]
BASE = 'https://example.test'


def controls():
    for name, raw, encoded, shortened in PUNCTUATION:
        destination = raw.replace('<', r'\<').replace('>', r'\>')
        surfaces = [
            ('markdown', '[Alpha](<' + destination + '>)', {'answer_format': 'markdown'}),
            ('html', '<a href="' + html.escape(raw, quote=True) + '">Alpha</a>',
             {'answer_format': 'html'}),
            ('metadata', '', {'links': [{'url': raw, 'title': 'Alpha'}]}),
            ('plain-encoded', BASE + encoded, {'answer_format': 'plain_text'}),
            ('autolink', '<' + BASE + encoded + '>', {'answer_format': 'markdown'}),
        ]
        for surface, artifact, metadata in surfaces:
            for expected, target in [(True, encoded), (False, shortened)]:
                yield {
                    'id': 'resource-uri-punctuation-' + name + '-' + surface
                          + ('-same' if expected else '-shortened'),
                    'category': 'format-normalization',
                    'failure_mode': 'false-negative' if expected else 'false-positive',
                    'root_cause': ('Resource readers own source wrappers; decoded URI angle and quote '
                                   'characters remain URL data and never alias a shortened resource.'),
                    'case': {'url': BASE + '/base', 'task_kind': 'resource',
                             'content_score_applicable': False,
                             'expected_resources': [{'urls': [BASE + target]}]},
                    'artifact': artifact, 'metadata': metadata, 'ok': True,
                    'expected_task_pass': expected,
                    'expected_metrics': {'resource_candidate_urls': [BASE + encoded]},
                }
    for expected, target in [(True, '/%22item%22'), (False, '/item')]:
        yield {
            'id': 'resource-uri-punctuation-quote-looking-destination-'
                  + ('same' if expected else 'shortened'),
            'category': 'format-normalization',
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'root_cause': ('A quote-looking bare Markdown destination is a URI, '
                           'not a quoted source wrapper or optional title.'),
            'case': {'url': BASE + '/base', 'task_kind': 'resource',
                     'content_score_applicable': False,
                     'expected_resources': [{'urls': [BASE + target]}]},
            'artifact': '[Alpha]("item")', 'metadata': {'answer_format': 'markdown'},
            'ok': True, 'expected_task_pass': expected,
            'expected_metrics': {'resource_candidate_urls': [BASE + '/%22item%22']},
        }
    for representation, artifact in [('plain_text', BASE + "/photo'.png"),
                                     ('markdown', '<' + BASE + "/photo'.png>")]:
        for expected, target in [(True, "/photo'.png"), (False, '/photo')]:
            yield {
                'id': 'resource-uri-punctuation-image-apostrophe-' + representation
                      + ('-same' if expected else '-shortened'),
                'category': 'format-normalization',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': 'Literal and angle-wrapped image URI answers retain legal apostrophes.',
                'case': {'url': BASE + '/base', 'task_kind': 'resource',
                         'content_score_applicable': False,
                         'expected_resources': [{'urls': [BASE + target]}],
                         'resource_output_contract': {'candidate_scope': 'image_answer_fields',
                                                      'unexpected': 'reject'}},
                'artifact': artifact, 'metadata': {'answer_format': representation},
                'ok': True, 'expected_task_pass': expected,
                'expected_metrics': {'resource_candidate_urls': [BASE + "/photo'.png"]},
            }


CONTROLS = list(controls())


@pytest.mark.parametrize('row', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_uri_punctuation_survives_resource_normalization(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=row['ok'],
                            metadata=row['metadata'])
    assert task_passed(row['ok'], metrics, 'resource') is row['expected_task_pass']
    for key, expected in row['expected_metrics'].items():
        assert metrics[key] == expected
