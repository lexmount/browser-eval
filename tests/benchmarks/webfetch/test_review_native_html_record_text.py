"""Rendered DOM text retains literal syntax; HTML elements alone format it."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


def _contract(schema):
    return {'version': 1, 'schema': schema, 'minimum_records': 1, 'maximum_records': 1}


CASES = [
    ('literal-stars-price', 'product', '<article><p>Widget</p><p>**$10.00**</p></article>', False, None),
    ('literal-italic-price', 'product', '<article><p>Widget</p><p>*$10.00*</p></article>', False, None),
    ('literal-underscore-price', 'product', '<article><p>Widget</p><p>__$10.00__</p></article>', False, None),
    ('literal-tag-price', 'product', '<article><p>Widget</p><p>&lt;b&gt;$10.00&lt;/b&gt;</p></article>', False, None),
    ('literal-link-price', 'product', '<article><p>Widget</p><p>[$10.00](/cost)</p></article>', False, None),
    ('bold-price', 'product', '<article><p>Widget</p><p><b>$10.00</b></p></article>', True, {'name': 'Widget', 'price': '$10.00'}),
    ('linked-price', 'product', '<article><p>Widget</p><p><a href="/cost">$10.00</a></p></article>', True, {'name': 'Widget', 'price': '$10.00'}),
    ('literal-title-stars', 'product', '<article><p>**Widget**</p><p>$10.00</p></article>', True, {'name': '**Widget**', 'price': '$10.00'}),
    ('literal-title-heading', 'product', '<article><p># Widget</p><p>$10.00</p></article>', True, {'name': '# Widget', 'price': '$10.00'}),
    ('literal-title-list', 'product', '<article><p>- Widget</p><p>$10.00</p></article>', True, {'name': '- Widget', 'price': '$10.00'}),
    ('literal-title-number', 'product', '<article><p>1. Widget</p><p>$10.00</p></article>', True, {'name': '1. Widget', 'price': '$10.00'}),
    ('literal-title-link', 'product', '<article><p>[Widget](/item/1)</p><p>$10.00</p></article>', True, {'name': '[Widget](/item/1)', 'price': '$10.00'}),
    ('literal-title-tag', 'product', '<article><p>&lt;b&gt;Widget&lt;/b&gt;</p><p>$10.00</p></article>', True, {'name': '<b>Widget</b>', 'price': '$10.00'}),
    ('formatted-title', 'product', '<article><p><b>Widget</b></p><p>$10.00</p></article>', True, {'name': 'Widget', 'price': '$10.00'}),
    ('literal-catalog', 'catalog_product', '<article><h2># Widget</h2><p>$10.00</p><p>**Durable** [manual](/docs) `code`</p><p>3 reviews</p></article>',
     True, {'title': '# Widget', 'price': '$10.00', 'description': '**Durable** [manual](/docs) `code`', 'reviews': '3'}),
    ('formatted-catalog', 'catalog_product', '<article><h2>Widget</h2><p>$10.00</p><p><b>Durable</b> <a href="/docs">manual</a> <code>code</code></p><p>3 reviews</p></article>',
     True, {'title': 'Widget', 'price': '$10.00', 'description': 'Durable manual code', 'reviews': '3'}),
    ('literal-catalog-heading', 'catalog_product', '<article><h2>Widget</h2><p>$10.00</p><p># Details</p><p>Durable device</p><p>3 reviews</p></article>',
     True, {'title': 'Widget', 'price': '$10.00', 'description': '# Details Durable device', 'reviews': '3'}),
    ('literal-description', 'name_description', '<section><p># Widget</p><p>**Durable** [manual](/docs)</p></section>',
     True, {'name': '# Widget', 'description': '**Durable** [manual](/docs)'}),
    ('literal-list-description', 'name_description', '<ul><li><p>[Widget](/item/1)</p><p>**Durable** device</p></li></ul>',
     True, {'name': '[Widget](/item/1)', 'description': '**Durable** device'}),
    ('literal-review', 'review', '<article><p>2026-01-01</p><p>**Great** service</p><p># Still this review</p></article>',
     True, {'date': '2026-01-01', 'text': '**Great** service # Still this review'}),
]


@pytest.mark.parametrize('identifier,schema,text,passed,fields', CASES)
def test_native_html_record_fields_preserve_the_actual_displayed_text(identifier, schema, text, passed, fields):
    contract = _contract(schema)
    direct = evaluate_stable_task(text, contract)
    assert direct['stable_task_pass'] is passed
    result = evaluate_response({'url': 'https://example.test/', 'acceptance_contract': contract}, text.encode())
    assert result['passed'] is passed
    if fields is not None:
        assert direct['stable_records'][0]['fields'] == fields
        assert result['metrics']['stable_records'][0]['fields'] == fields


def scoring_controls():
    for identifier, schema, text, passed, fields in CASES:
        yield {'id': 'runtime15-' + identifier, 'category': 'format-normalization',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'native_html_record_text_is_not_reparsed_as_markdown',
               'case': {'url': 'https://example.test/', 'acceptance_contract': _contract(schema)},
               'artifact': text, 'ok': True, 'expected_task_pass': passed,
               **({'expected_metrics': {'stable_records': [{'fields': fields, 'valid': True, 'errors': []}]}}
                  if fields is not None else {})}
