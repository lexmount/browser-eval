"""Declared literal text admits explicit fields, never rendered markup links."""
import hashlib

import pytest

from lexbench_webfetch.dynamic_lists import extract_linked_products, extract_named_entries, named_entry_source_pair_hash
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
@pytest.mark.parametrize('artifact', ['[Alpha](https://shop.example/item/1) $10',
    '<a href="https://shop.example/item/1">Alpha</a> <span>$10</span>',
    '```\n[Alpha](https://shop.example/item/1) $10\n```'])
def test_literal_markup_does_not_deliver_a_rendered_link(schema, artifact):
    case = record_task(schema, **({'price_patterns': [r'\$\d+']} if schema == 'linked_product' else {}))
    extractor = extract_linked_products if schema == 'linked_product' else extract_named_entries
    assert extractor(artifact, case['acceptance_contract'], case['url'], representation='plain_text') == []
    assert not evaluate_response(case, artifact.encode(), metadata={'answer_format': 'plain_text'})['passed']


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
def test_literal_label_url_and_price_fields_remain_usable(schema):
    case = record_task(schema, **({'price_patterns': [r'\$\d+']} if schema == 'linked_product' else {}))
    body = 'Alpha\nhttps://shop.example/item/1\n$10'
    extractor = extract_linked_products if schema == 'linked_product' else extract_named_entries
    entries = extractor(body, case['acceptance_contract'], case['url'], representation='plain_text')
    assert entries[0]['title'] == 'Alpha'
    assert evaluate_response(case, body.encode(), metadata={'answer_format': 'plain_text'})['passed']
    assert not evaluate_response(case, body.replace('/item/1', '/other/1').encode(), metadata={'answer_format': 'plain_text'})['passed']


def test_literal_markup_in_a_name_is_not_normalized_into_a_frozen_name():
    case = record_task()
    contract = case['acceptance_contract']
    pair = named_entry_source_pair_hash('Alpha', 'https://shop.example/item/1', contract)
    contract.update(source_record_pair_hashes=[pair], source_record_pairs_sha256=hashlib.sha256(pair.encode()).hexdigest())
    for title in ['**Alpha**', '<b>Alpha</b>', 'Alpha&amp;']:
        body = title + '\nhttps://shop.example/item/1'
        entries = extract_named_entries(body, contract, case['url'], representation='plain_text')
        assert entries[0]['title'] == title
        assert not evaluate_response(case, body.encode(), metadata={'answer_format': 'plain_text'})['passed']
    assert evaluate_response(case, b'Alpha\nhttps://shop.example/item/1', metadata={'answer_format': 'plain_text'})['passed']


def test_explicit_metadata_named_pairs_keep_their_plain_text_contract():
    case = record_task()
    metadata = {'answer_format': 'plain_text', 'links': [{'url': '/item/1', 'text': 'Alpha'}]}
    assert evaluate_response(case, b'Useful introduction', metadata=metadata)['passed']
    metadata['links'][0]['url'] = '/other/1'
    assert not evaluate_response(case, b'Useful introduction', metadata=metadata)['passed']


def test_plain_product_price_does_not_strip_literal_html_or_markdown():
    case = record_task('linked_product', price_patterns=[r'\$\d+'])
    for price in ['**$10**', '<span>$10</span>']:
        body = 'Alpha\nhttps://shop.example/item/1\n' + price
        assert not evaluate_response(case, body.encode(), metadata={'answer_format': 'plain_text'})['passed']


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
@pytest.mark.parametrize('body', ['[Alpha](https://shop.example/item/1) $10',
    '<article>[Alpha](https://shop.example/item/1) $10</article>'])
def test_declared_html_does_not_manufacture_markdown_links(schema, body):
    case = record_task(schema, **({'price_patterns': [r'\$\d+']} if schema == 'linked_product' else {}))
    extractor = extract_linked_products if schema == 'linked_product' else extract_named_entries
    assert extractor(body, case['acceptance_contract'], case['url'], representation='html') == []
    assert not evaluate_response(case, body.encode(), metadata={'answer_format': 'html'})['passed']
    assert evaluate_response(case, b'[Alpha](https://shop.example/item/1) $10',
                             metadata={'answer_format': 'markdown'})['passed']


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
def test_html_text_retains_literal_markdown_but_parses_actual_html_markup(schema):
    case = record_task(schema, **({'price_patterns': [r'\$\d+']} if schema == 'linked_product' else {}))
    contract = case['acceptance_contract']
    pair = named_entry_source_pair_hash('Alpha', 'https://shop.example/item/1', contract)
    contract.update(source_record_pair_hashes=[pair], source_record_pairs_sha256=hashlib.sha256(pair.encode()).hexdigest())
    bad = '<article><a href="/item/1">**Alpha**</a><span>$10</span></article>'
    extractor = extract_linked_products if schema == 'linked_product' else extract_named_entries
    assert extractor(bad, contract, case['url'], representation='html')[0]['title'] == '**Alpha**'
    assert not evaluate_response(case, bad.encode(), metadata={'answer_format': 'html'})['passed']
    good = bad.replace('**Alpha**', '<b>Alpha</b>')
    assert evaluate_response(case, good.encode(), metadata={'answer_format': 'html'})['passed']


def test_actual_html_anchor_inside_literal_backticks_is_still_an_anchor():
    case = record_task()
    body = '<p>`<a href="/item/1">Alpha</a>`</p>'
    assert evaluate_response(case, body.encode(), metadata={'answer_format': 'html'})['passed']


def test_actual_html_price_markup_and_literal_markdown_price_are_distinct():
    case = record_task('linked_product', price_patterns=[r'\$\d+'],
                       entry_scope={'html_selector': '.product', 'price_selector': '.price'})
    body = '<div class="product"><a href="/item/1">Alpha</a><span class="price">**$10**</span></div>'
    assert not evaluate_response(case, body.encode(), metadata={'answer_format': 'html'})['passed']
    assert evaluate_response(case, body.replace('**$10**', '<b>$10</b>').encode(), metadata={'answer_format': 'html'})['passed']


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
def test_html_exclusions_compare_actual_text_not_markdown_rendering(schema):
    case = record_task(schema, record_name_exclusions=['Alpha'],
                       **({'price_patterns': [r'\$\d+']} if schema == 'linked_product' else {}))
    body = '<article><a href="/item/1">**Alpha**</a><span>$10</span></article>'
    result = evaluate_response(case, body.encode(), metadata={'answer_format': 'html'})
    assert result['passed']
    assert result['metrics']['stable_excluded_record_names'] == []
    result = evaluate_response(case, body.replace('**Alpha**', '<b>Alpha</b>').encode(), metadata={'answer_format': 'html'})
    assert not result['passed']
    assert result['metrics']['stable_excluded_record_names'] == ['Alpha']
