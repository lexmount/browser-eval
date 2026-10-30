"""Product field extraction separates admitted identities from excluded audits."""
import pytest

from lexbench_webfetch.dynamic_lists import extract_linked_products
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task


def product_case(scoped=False):
    return record_task('linked_product', price_patterns=[r'\$\d+(?:\.\d{2})?'],
                       record_name_exclusions=['Generic'], **({'entry_scope': {
                           'html_selector': '.product', 'price_selector': '.price'}} if scoped else {}))


def product(title, representation):
    if representation == 'markdown':
        return f'[{title}](/item/1) $10'
    return f'<article class="product"><a href="/item/1">{title}</a><span class="price">$10</span></article>'


@pytest.mark.parametrize('representation', ['markdown', 'html', 'scoped'])
@pytest.mark.parametrize('reverse', [False, True])
def test_excluded_auxiliary_product_does_not_replace_valid_same_url(representation, reverse):
    case = product_case(representation == 'scoped')
    names = ['Alpha', 'Generic']
    if reverse:
        names.reverse()
    body = '\n'.join(product(name, representation) for name in names)
    contract = case['acceptance_contract']
    records = extract_linked_products(body, contract, case['url'])
    assert [record['title'] for record in records] == ['Alpha']
    audit = extract_linked_products(body, contract, case['url'], include_excluded=True)
    assert sorted(record['title'] for record in audit) == ['Alpha', 'Generic']
    result = evaluate_response(case, body.encode())
    assert result['passed']
    assert result['metrics']['stable_complete_record_count'] == 1
    assert result['metrics']['stable_excluded_record_names'] == ['Generic']
    # Descriptive metadata cannot override actual admitted product fields.
    assert evaluate_response(case, body.encode(), metadata={'links': [
        {'url': '/item/1', 'title': 'Generic'}]})['passed']


@pytest.mark.parametrize('representation', ['markdown', 'html', 'scoped'])
def test_excluded_product_alone_is_only_auditable(representation):
    case = product_case(representation == 'scoped')
    body = product('Generic', representation)
    assert extract_linked_products(body, case['acceptance_contract'], case['url']) == []
    assert len(extract_linked_products(body, case['acceptance_contract'], case['url'], include_excluded=True)) == 1
    result = evaluate_response(case, body.encode())
    assert not result['passed']
    assert result['metrics']['stable_excluded_record_names'] == ['Generic']


def test_nonadjacent_same_url_product_occurrences_preserve_the_admitted_name():
    case = product_case()
    body = '[Generic](/item/1) $10\n[Beta](/item/2) $20\n[Alpha](/item/1) $10'
    records = extract_linked_products(body, case['acceptance_contract'], case['url'])
    assert {record['title'] for record in records} == {'Alpha', 'Beta'}


@pytest.mark.parametrize('reverse', [False, True])
def test_one_scoped_product_card_retains_both_admission_and_auxiliary_audit(reverse):
    case = product_case(True)
    names = ['Alpha', 'Generic']
    if reverse:
        names.reverse()
    body = '<article class="product">' + ''.join(f'<a href="/item/1">{name}</a>' for name in names) + '<span class="price">$10</span></article>'
    records = extract_linked_products(body, case['acceptance_contract'], case['url'])
    assert [record['title'] for record in records] == ['Alpha']
    result = evaluate_response(case, body.encode())
    assert result['passed']
    assert result['metrics']['stable_excluded_record_names'] == ['Generic']
