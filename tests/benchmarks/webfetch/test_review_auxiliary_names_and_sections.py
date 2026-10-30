"""Auxiliary labels and independent sections cannot replace record fields."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('native', [False, True])
def test_auxiliary_same_destination_never_erases_an_independent_valid_name(reverse, native):
    case = record_task(record_name_exclusions=['FreeTV'])
    names = ['Alpha Show', 'FreeTV']
    if reverse:
        names.reverse()
    text = '\n'.join(f'<a href="/item/1">{name}</a>' if native else f'[{name}](/item/1)' for name in names)
    result = evaluate_response(case, text.encode())
    assert result['passed']
    assert result['metrics']['stable_complete_record_count'] == 1
    assert result['metrics']['stable_excluded_record_names'] == ['FreeTV']
    assert not evaluate_response(case, b'[FreeTV](/item/1)')['passed']


@pytest.mark.parametrize('section', ['<h2>Shipping</h2><p>$10.00</p>',
    '<div><h3>Membership</h3></div><p>$10.00</p>',
    '<section><p>$10.00</p></section>', '<aside>$10.00</aside>',
    '<article><span>$10.00</span></article>',
    '<h2>Shipping</h2><p>Unavailable</p>'])
@pytest.mark.parametrize('wrapper', [False, True])
def test_html_product_fields_stop_at_independent_sections(section, wrapper):
    case = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'],
                       price_unavailable_patterns=['Unavailable'])
    anchor = '<a href="/item/1">Widget</a>'
    if wrapper:
        anchor = '<div><span>' + anchor + '</span></div>'
    bad = '<div>' + anchor + section + '</div>'
    good = '<div>' + anchor + '<div><span>$9.00</span></div>' + section + '</div>'
    assert not evaluate_response(case, bad.encode())['passed']
    assert evaluate_response(case, good.encode())['passed']


def test_section_boundary_inside_title_wrapper_still_stops_later_outer_price():
    case = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    bad = '<div><div><a href="/item/1">Widget</a><h2>Other costs</h2></div><p>$10.00</p></div>'
    assert not evaluate_response(case, bad.encode())['passed']
    good = '<div><h2><a href="/item/1">Widget</a></h2><div><span>$10.00</span></div></div>'
    assert evaluate_response(case, good.encode())['passed']


def test_price_before_title_must_belong_to_the_same_section():
    case = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    assert evaluate_response(case, b'<div><span>$10.00</span><a href="/item/1">Widget</a></div>')['passed']
    assert not evaluate_response(case, b'<div><span>$10.00</span><h2>Next section</h2><a href="/item/1">Widget</a></div>')['passed']


@pytest.mark.parametrize('owner', ['article', 'li', 'section'])
@pytest.mark.parametrize('wrapper', ['{}', '<div>{}</div>'])
def test_semantic_product_owner_accepts_internal_price_section(owner, wrapper):
    case = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    anchor = '<a href="/item/1">Widget</a>'
    price = wrapper.format('<section class="price"><p>$10.00</p></section>')
    good = f'<{owner}>{anchor}{price}</{owner}>'
    assert evaluate_response(case, good.encode())['passed']
    foreign = price.replace('<p>', '<h2>Other charges</h2><p>')
    assert not evaluate_response(case, f'<{owner}>{anchor}{foreign}</{owner}>'.encode())['passed']
    independent = price.replace('section', 'article')
    assert not evaluate_response(case, f'<{owner}>{anchor}{independent}</{owner}>'.encode())['passed']


def test_another_product_cannot_supply_an_internal_section_price():
    case = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    bad = '<article><a href="/item/1">Widget</a><section><a href="/item/2">Details</a><p>$10.00</p></section></article>'
    assert not evaluate_response(case, bad.encode())['passed']
