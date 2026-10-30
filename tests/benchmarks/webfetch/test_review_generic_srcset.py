"""Responsive image URLs retain one resource identity on every answer surface."""
import html
import json

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def task(urls):
    return {'id': 'responsive-resources', 'name': 'Responsive resources',
            'url': 'https://example.test/page', 'task_kind': 'resource',
            'content_score_applicable': False,
            'expected_resources': [{'id': str(index), 'urls': [url]}
                                   for index, url in enumerate(urls)]}


def surface(markup, representation):
    if representation == 'frame':
        return '<iframe srcdoc="' + html.escape(markup, quote=True) + '"></iframe>'
    if representation.startswith('json-'):
        return json.dumps({representation.split('-')[1]: markup})
    return markup


@pytest.mark.parametrize('tag', ['img', 'source'])
@pytest.mark.parametrize('representation', ['direct', 'frame', 'json-html', 'json-content'])
@pytest.mark.parametrize('urls', [
    ['/required.png', '/large.png'],
    ['/set/a,b.png', '/image?crop=10,20&a=1&b=2'],
])
def test_generic_srcset_uses_shared_parser_on_native_and_wrapped_markup(tag, representation, urls):
    value = ', '.join(f'{url} {index + 1}x' for index, url in enumerate(urls))
    markup = f'<{tag} srcset="{html.escape(value, quote=True)}">'
    result = evaluate_response(task(urls), surface(markup, representation).encode())
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == sorted('https://example.test' + url for url in urls)
    assert result['metrics']['resource_precision'] == 1
    assert not evaluate_response(task(['/missing.png']), surface(markup, representation).encode())['passed']


@pytest.mark.parametrize('representation', ['direct', 'frame', 'json-html', 'json-content'])
def test_generic_srcset_excludes_data_urls_and_deduplicates_same_url(representation):
    markup = '<source srcset="data:image/png;base64,AA== 1x, /required.png 2x, /required.png 3x">'
    result = evaluate_response(task(['/required.png']), surface(markup, representation).encode())
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == ['https://example.test/required.png']
    assert result['metrics']['resource_precision'] == 1


@pytest.mark.parametrize('markup', ['<img data-srcset="/required.png 1x">',
                                   '<a srcset="/required.png 1x">Link</a>'])
def test_non_resource_attribute_syntax_cannot_supply_a_responsive_image(markup):
    assert not evaluate_response(task(['/required.png']), markup.encode())['passed']


def test_unquoted_descriptorless_srcset_is_valid_html():
    assert evaluate_response(task(['/required.png']), b'<img srcset=/required.png>')['passed']


def test_typed_image_contract_keeps_its_unexpected_candidate_policy():
    case = task(['/required.png'])
    mixed = b'<img srcset="/required.png 1x, /noise.png 2x">'
    assert evaluate_response(case, mixed)['passed']
    case['resource_output_contract'] = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
    assert not evaluate_response(case, mixed)['passed']
    assert evaluate_response(case, b'<img srcset="/required.png 1x">')['passed']
