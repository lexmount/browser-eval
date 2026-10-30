"""Inline Markdown formatting does not change a DOM-owned product field."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task


def product_task(**rules):
    return record_task('linked_product', price_patterns=[r'\$\s*\d+(?:\.\d{2})?'], **rules)


@pytest.mark.parametrize('price', ['$10.00', '**$10.00**', '*$10.00*', '__$10.00__', '`$10.00`', r'\$10.00'])
@pytest.mark.parametrize('layout', ['{}<br>', '<div>{}</div>', '<article>{}</article>'])
def test_mixed_markdown_product_price_keeps_field_format_equivalence(price, layout):
    text = layout.format('[Alpha](/item/1)\n' + price)
    result = evaluate_response(product_task(), text.encode())
    assert result['passed']
    assert result['metrics']['stable_complete_record_count'] == 1
    assert not evaluate_response(product_task(), text.replace('10.00', 'unknown').encode())['passed']


@pytest.mark.parametrize('price', ['**$10.00**', '`$10.00`'])
def test_explicit_html_price_fields_use_the_same_inline_format_projection(price):
    case = product_task(entry_scope={'html_selector': '.product', 'price_selector': '.price'})
    good = f'<div class="product"><a href="/item/1">Alpha</a><span class="price">{price}</span></div>'
    assert evaluate_response(case, good.encode())['passed']
    assert not evaluate_response(case, good.replace('class="price"', 'class="other"').encode())['passed']


@pytest.mark.parametrize('price', ['**$10.00**', '`$10.00`'])
def test_formatted_fields_preserve_section_boundaries_and_visible_ownership(price):
    case = product_task()
    anchor = '<a href="/item/1">Alpha</a>'
    for body in [f'<article>{anchor}<h2>Other charges</h2><span>{price}</span></article>',
                 f'<article>{anchor}<section><h2>Other charges</h2><span>{price}</span></section></article>',
                 f'<article>{anchor}<span title="{price}">Details</span></article>',
                 f'<article>{anchor}<span hidden>{price}</span></article>',
                 f'<article>{anchor}<span>Other charges {price}</span></article>']:
        assert not evaluate_response(case, body.encode())['passed']
    assert evaluate_response(case, f'<article>{anchor}<span>{price}</span><h2>Other charges</h2></article>'.encode())['passed']
