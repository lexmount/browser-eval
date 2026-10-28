"""Public scorer controls for record ownership and equivalent answer formats."""
import html
import json

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.list_verification import name_matches


def task(**fields):
    return {'id': 'record-format', 'name': 'Record format', 'url': 'https://shop.example/catalog',
            'category': 'web', 'expected_any': [], **fields}


def record_task(schema='named_link', **rules):
    return task(acceptance_contract={
        'version': 1, 'schema': schema, 'minimum_records': 1,
        'hosts': ['shop.example'], 'entry_path': r'/item/\d+',
        'stable_text_anchors': [], **rules})


@pytest.mark.parametrize('separator,expected', [
    ('', True), ('\n', True), ('[Other record](/item/2)\n\n', False),
    ('A separate paragraph.\n\n', False),
])
def test_image_card_only_owns_an_adjacent_heading(separator, expected):
    answer = '[![First title](https://images.example/1.jpg)](/item/1)\n\n' + separator + '## First title'
    result = evaluate_response(record_task(), answer.encode())
    first = next((row for row in result['metrics']['stable_records']
                  if row['fields']['url'] == 'https://shop.example/item/1'), None)
    assert bool(first and first['fields']['title'] == 'First title') is expected


def test_image_heading_card_uses_balanced_links_and_optional_titles():
    answer = '[![First title](https://images.example/figure(one).png "Image")](/item/model(one) "Details")\n\n## First title'
    assert evaluate_response(record_task(entry_path=r'/item/[^/]+'), answer.encode())['passed']


@pytest.mark.parametrize('tag', ['span', 'li', 'aside', 'custom-card'])
@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
def test_visibility_does_not_depend_on_container_tag_heuristic(tag, schema):
    fields = {'price_patterns': [r'\$\d+\.\d{2}']} if schema == 'linked_product' else {}
    visible = f'<{tag}>\n[Widget](/item/1) $10.00\n</{tag}>'
    hidden = visible.replace(f'<{tag}>', f'<{tag} hidden>', 1)
    assert evaluate_response(record_task(schema, **fields), visible.encode())['passed']
    assert not evaluate_response(record_task(schema, **fields), hidden.encode())['passed']


@pytest.mark.parametrize('link', [
    '[Widget](/item/1 "Details")', "[Widget](/item/1 'Details')",
    '[Widget](/item/model(one))', '<a href=/item/1>Widget</a>',
])
def test_products_use_balanced_markdown_and_native_html_links(link):
    contract = record_task('linked_product', entry_path=r'/item/[^/]+', price_patterns=[r'\$\d+\.\d{2}'])
    assert evaluate_response(contract, (link + ' $10.00').encode())['passed']


def test_html_title_metadata_cannot_manufacture_another_product():
    contract = record_task('linked_product', minimum_records=2, price_patterns=[r'\$\d+\.\d{2}'])
    answer = '<a href="/item/1" title="[Other](/item/2)">Widget</a> $10.00'
    result = evaluate_response(contract, answer.encode())
    assert not result['passed']
    assert result['metrics']['stable_complete_record_count'] == 1


def test_hidden_malformed_link_does_not_reject_a_visible_valid_record():
    answer = '<span hidden>\n[Broken](https://[invalid/item/2)\n</span>\n[Widget](/item/1)'
    assert evaluate_response(record_task(), answer.encode())['passed']


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
@pytest.mark.parametrize('literal', [
    '`[Widget](/item/1 "Details") $10.00`',
    '```md\n[Widget](/item/1 "Details") $10.00\n```',
    '```md\n[Widget](/item/1 "Details") $10.00\n````',
    '```md\n[Widget](/item/1 "Details") $10.00',
])
def test_literal_link_examples_cannot_introduce_records(schema, literal):
    fields = {'price_patterns': [r'\$\d+\.\d{2}']} if schema == 'linked_product' else {}
    contract = record_task(schema, **fields)
    assert not evaluate_response(contract, literal.encode())['passed']
    assert evaluate_response(contract, '[Widget](/item/1 "Details") `$10.00`'.encode())['passed']


def test_fenced_price_example_does_not_complete_a_real_product():
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    answer = '[Widget](/item/1)\n```md\n[Other](/item/2) $10.00\n```'
    assert not evaluate_response(contract, answer.encode())['passed']


@pytest.mark.parametrize('tail', [
    '\n\n## Shipping\nDelivery costs $10.00',
    '\n\nShipping\n--------\nDelivery costs $10.00',
    '\n\n---\nDelivery costs $10.00',
    '\n- Delivery costs $10.00',
    '<h2>Shipping</h2><p>Delivery costs $10.00</p>',
])
def test_missing_product_price_cannot_borrow_from_another_section(tail):
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    assert not evaluate_response(contract, ('[Widget](/item/1)' + tail).encode())['passed']
    assert evaluate_response(contract, ('[Widget](/item/1) $9.00' + tail).encode())['passed']


@pytest.mark.parametrize('returned,reference,expected', [
    ('Example Phone Pro', 'Example Phone', False),
    ('Example Phone Ultra', 'Example Phone', False),
    ('Example Phone 2025 Pro', 'Example Phone', False),
    ('1. The Book — 1999 5 stars', 'The Book', True),
    ('The Book 4.5 stars', 'The Book', True),
    ('Free Reacher TV', 'Reacher', True),
    ('Top RatedHaunted House Candle', 'Haunted House Candle', True),
])
def test_reference_name_suffixes_are_declared_metadata_not_arbitrary_models(returned, reference, expected):
    assert name_matches(returned, reference) is expected
    benchmark = {'mode': 'dynamic_list_structure', 'verification_scope': 'first_three_name_detail_pairs',
                 'hosts': ['shop.example'], 'entry_path': r'/item/\d+', 'minimum_entries': 1}
    result = evaluate_response(task(source_benchmark=benchmark),
                               f'[{returned}](/item/1)'.encode(),
                               list_reference={'items': [{'url': 'https://shop.example/item/1', 'title': reference}]})
    assert result['passed'] is expected


@pytest.mark.parametrize('quote', ['', '"', "'"])
@pytest.mark.parametrize('kind', ['image', 'link'])
@pytest.mark.parametrize('surface', ['direct', 'json-html', 'json-content', 'frame'])
def test_html_attribute_formats_share_one_resource_reader(quote, kind, surface):
    path = '/target.png' if kind == 'image' else '/manual.pdf?a=1&b=2'
    escaped = html.escape(path, quote=True)
    markup = (f'<img src={quote}{escaped}{quote}>' if kind == 'image'
              else f'<a href={quote}{escaped}{quote}>Download</a>')
    if surface.startswith('json-'):
        answer = json.dumps({surface.split('-')[1]: markup})
    elif surface == 'frame':
        answer = '<iframe srcdoc="' + html.escape(markup, quote=True) + '"></iframe>'
    else:
        answer = markup
    result = evaluate_response(task(task_kind='resource', content_score_applicable=False,
                                    expected_resources=[{'urls': [path]}]), answer.encode())
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == ['https://shop.example' + path]
    assert result['metrics']['resource_precision'] == 1


@pytest.mark.parametrize('attribute', ['hidden', 'style="display:none"', 'style="visibility:hidden"'])
def test_hidden_frame_ancestors_do_not_publish_their_documents(attribute):
    frame = '<iframe srcdoc="&lt;img src=/target.png&gt;"></iframe>'
    image_task = task(task_kind='resource', content_score_applicable=False,
                      expected_resources=[{'urls': ['/target.png']}],
                      resource_output_contract={'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'})
    assert not evaluate_response(image_task, f'<div {attribute}>{frame}</div>'.encode())['passed']
    assert evaluate_response(image_task, frame.encode())['passed']
    extra = '<iframe srcdoc="&lt;img src=/other.png&gt;"></iframe>'
    assert evaluate_response(image_task, (frame + f'<div {attribute}>{extra}</div>').encode())['passed']


def test_frame_visibility_uses_the_shared_last_inline_style_declaration():
    frame = '<iframe style="display:none; display:block" srcdoc="&lt;img src=/target.png&gt;"></iframe>'
    image_task = task(task_kind='resource', content_score_applicable=False,
                      expected_resources=[{'urls': ['/target.png']}],
                      resource_output_contract={'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'})
    assert evaluate_response(image_task, frame.encode())['passed']


@pytest.mark.parametrize('field', ['html', 'content'])
@pytest.mark.parametrize('link', ['[Download](/manual.pdf "File")', '![Preview](/manual.pdf)'])
def test_json_wrapped_markdown_keeps_resource_destinations(field, link):
    result = evaluate_response(task(task_kind='resource', content_score_applicable=False,
                                    expected_resources=[{'urls': ['/manual.pdf']}]),
                               json.dumps({field: link}).encode())
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == ['https://shop.example/manual.pdf']
