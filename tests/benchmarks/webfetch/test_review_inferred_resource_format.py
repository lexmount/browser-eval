"""Inferred resource answers keep native HTML and Markdown literal semantics."""
import copy
import json

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def controls():
    for image_only in [False, True]:
        case = {'url': 'https://example.test/page', 'task_kind': 'resource',
                'content_score_applicable': False,
                'expected_resources': [{'urls': ['/target.png']}]}
        if image_only:
            case['resource_output_contract'] = {'candidate_scope': 'image_answer_fields',
                                                'unexpected': 'reject'}
        scope = 'image' if image_only else 'generic'
        surfaces = [
            ('active', '[Image](/target.png)', True),
            ('fenced', '```\n[Image](/target.png)\n```', False),
            ('inline', '`[Image](/target.png)`', False),
            ('indented', '    [Image](/target.png)', False),
            ('fenced-html', '```html\n<img src="/target.png">\n```', False),
        ]
        for representation in [None, 'markdown']:
            for name, artifact, expected in surfaces:
                yield row(scope + '-' + (representation or 'inferred') + '-' + name,
                          case, artifact, representation, expected)
        for representation in [None, 'html']:
            for markup, expected in [('<img src="/target.png">', True),
                                     ('[Image](/target.png)', False)]:
                yield row(scope + '-' + (representation or 'inferred') + '-native-html-'
                          + ('image' if expected else 'literal-markdown'), case,
                          '<div>`' + markup + '`</div>', representation, expected)
        yield row(scope + '-native-json', case, json.dumps({'images': ['/target.png']}), None, True)
        yield row(scope + '-plain-url', case, 'https://example.test/target.png', 'plain_text', True)


def row(identifier, case, artifact, representation, expected):
    return {
        'id': 'resource-inferred-format-' + identifier,
        'category': 'format-normalization',
        'failure_mode': 'false-negative' if expected else 'false-positive',
        'root_cause': ('Undeclared resource answers infer HTML or Markdown after native JSON detection; '
                       'Markdown literal examples do not deliver resources, and HTML backticks remain text.'),
        'case': copy.deepcopy(case), 'artifact': artifact, 'ok': True,
        'metadata': {'answer_format': representation} if representation else {},
        'expected_task_pass': expected,
    }


CONTROLS = list(controls())


@pytest.mark.parametrize('control', CONTROLS, ids=[item['id'] for item in CONTROLS])
def test_inferred_resource_format_owns_literal_examples(control):
    metrics = score_attempt(control['case'], control['artifact'].encode(), ok=control['ok'],
                            metadata=control['metadata'])
    assert task_passed(control['ok'], metrics, 'resource') is control['expected_task_pass']
