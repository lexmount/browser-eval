"""Visible title boundaries and typed JSON image answers survive conversion."""
import hashlib
import html
import json

import pytest

from lexbench_webfetch.dynamic_lists import named_entry_source_pair_hash
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_owned_surfaces import image_task
from tests.benchmarks.webfetch.test_review_record_formats import record_task


def bound_title_task(title):
    case = record_task()
    contract = case['acceptance_contract']
    pairs = [named_entry_source_pair_hash(title, 'https://shop.example/item/1', contract)]
    contract.update(source_record_pair_hashes=pairs,
                    source_record_pairs_sha256=hashlib.sha256('\n'.join(pairs).encode()).hexdigest())
    return case


@pytest.mark.parametrize('label', ['Alpha<br>Beta', '<div>Alpha</div><div>Beta</div>',
                                  'Alpha<p>Beta</p>', 'Alpha<br/>Beta'])
def test_html_block_boundaries_match_markdown_source_names(label):
    case = bound_title_task('Alpha Beta')
    assert evaluate_response(case, f'<a href="/item/1">{label}</a>'.encode())['passed']
    assert evaluate_response(case, b'[Alpha Beta](/item/1)')['passed']
    assert not evaluate_response(case, b'<a href="/item/1">AlphaBeta</a>')['passed']


def test_adjacent_inline_title_fragments_stay_adjacent():
    case = bound_title_task('AlphaBeta')
    assert evaluate_response(case, b'<a href="/item/1">Al<span>pha</span><b>Beta</b></a>')['passed']
    assert not evaluate_response(case, b'<a href="/item/1">Alpha<br>Beta</a>')['passed']


@pytest.mark.parametrize('tags', [['portrait'], [['portrait']], ['portrait', 'landscape']])
def test_image_descriptive_lists_are_not_image_urls(tags):
    metadata = {'images': [{'url': '/target.png', 'tags': tags, 'captions': ['A portrait']}]}
    result = evaluate_response(image_task(), b'', metadata=metadata)
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == ['https://shop.example/target.png']
    metadata['images'].append({'url': '/other.png', 'tags': tags})
    assert not evaluate_response(image_task(), b'', metadata=metadata)['passed']


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('branch', ['debug', 'requests', 'logs', 'diagnostics'])
def test_json_diagnostic_urls_do_not_supply_resource_answers(branch, image_only):
    case = image_task()
    if not image_only:
        case.pop('resource_output_contract')
    target = 'https://shop.example/target.png'
    assert not evaluate_response(case, json.dumps({branch: {'url': target}}).encode())['passed']
    assert evaluate_response(case, json.dumps({'images': [target], branch: {'url': target}}).encode())['passed']


@pytest.mark.parametrize('surface', ['images', 'html', 'content', 'list'])
def test_unprojected_json_image_answers_and_iframes(surface):
    target = 'https://shop.example/target.png'
    markup = '<iframe srcdoc="' + html.escape('<img src=/target.png>', quote=True) + '"></iframe>'
    value = [target] if surface == 'list' else {surface: [target] if surface == 'images' else markup}
    assert evaluate_response(image_task(), json.dumps(value).encode())['passed']
    if surface in {'html', 'content'}:
        assert not evaluate_response(image_task(), json.dumps({'debug': value}).encode())['passed']


def test_json_diagnostic_markup_cannot_bypass_the_json_owner():
    value = {'debug': {'html': "<iframe srcdoc='<img src=/target.png>'></iframe>"}}
    assert not evaluate_response(image_task(), json.dumps(value).encode())['passed']
