"""Generic resource discovery honors source format and explicit answer fields."""
import html
import json

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import task


def resource_case(url='/target.png'):
    return task(task_kind='resource', content_score_applicable=False,
                expected_resources=[{'urls': [url]}])


@pytest.mark.parametrize('body', ['<img src="/target.png">', '[Image](/target.png)',
    '![Image](/target.png)', json.dumps({'images': ['/target.png']}),
    json.dumps(['https://shop.example/target.png'])])
def test_plain_resource_examples_are_not_markup_or_structured_answers(body):
    assert not evaluate_response(resource_case(), body.encode(), metadata={'answer_format': 'plain_text'})['passed']
    assert evaluate_response(resource_case(), b'https://shop.example/target.png',
                             metadata={'answer_format': 'plain_text'})['passed']


@pytest.mark.parametrize('representation', ['plain_text', 'html', 'markdown'])
def test_typed_metadata_is_independent_of_literal_content_format(representation):
    metadata = {'answer_format': representation, 'links': [{'url': '/target.png'}]}
    assert evaluate_response(resource_case(), b'Introduction', metadata=metadata)['passed']
    metadata['links'][0]['url'] = '/other.png'
    assert not evaluate_response(resource_case(), b'Introduction', metadata=metadata)['passed']


@pytest.mark.parametrize('body', ['[Image](/target.png)', '![Image](/target.png)',
                                  '<p>[Image](/target.png)</p>', '{"images":["/target.png"]}'])
def test_declared_html_does_not_interpret_markdown_or_json(body):
    assert not evaluate_response(resource_case(), body.encode(), metadata={'answer_format': 'html'})['passed']
    assert evaluate_response(resource_case(), b'<img src="/target.png">', metadata={'answer_format': 'html'})['passed']


def test_markdown_resources_and_literal_examples_remain_distinct():
    for body in ['[Image](/target.png)', '![Image](/target.png)', '<img src="/target.png">']:
        assert evaluate_response(resource_case(), body.encode(), metadata={'answer_format': 'markdown'})['passed']
        assert not evaluate_response(resource_case(), ('```\n' + body + '\n```').encode(),
                                     metadata={'answer_format': 'markdown'})['passed']
    assert not evaluate_response(resource_case(), b'{"images":["/target.png"]}', metadata={'answer_format': 'markdown'})['passed']
    assert evaluate_response(resource_case(), b'{"images":["/target.png"]}')['passed']


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('representation', ['plain_text', 'html'])
@pytest.mark.parametrize('url', ['https://shop.example/target(one).png?a=1&amp;b=2',
                                'https://shop.example/target.png?a=(one)&amp;b=2'])
def test_literal_uri_values_keep_parentheses_and_entity_bytes(image_only, representation, url):
    case = resource_case(url)
    if image_only:
        case['resource_output_contract'] = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
    content = ('<p>' + html.escape(url) + '</p>') if representation == 'html' else url
    result = evaluate_response(case, content.encode(), metadata={'answer_format': representation})
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == [url]
    case['expected_resources'] = [{'urls': [url.replace('&amp;', '&')]}]
    assert not evaluate_response(case, content.encode(), metadata={'answer_format': representation})['passed']


def test_inline_frame_document_has_native_html_resource_ownership():
    def frame(body):
        return ('<iframe srcdoc="' + html.escape(body, quote=True) + '"></iframe>').encode()
    assert evaluate_response(resource_case(), frame('<img src="/target.png">'), metadata={'answer_format': 'html'})['passed']
    assert not evaluate_response(resource_case(), frame('[Image](/target.png)'), metadata={'answer_format': 'html'})['passed']
