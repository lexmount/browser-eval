"""Scoped fields and image-answer evidence use their declared visible owners."""
import html

import pytest

from lexbench_webfetch.dynamic_lists import extract_named_entries, visible_html_text
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task, task


def image_task():
    return task(task_kind='resource', content_score_applicable=False,
                expected_resources=[{'urls': ['/target.png']}],
                resource_output_contract={'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'})


@pytest.mark.parametrize('links', [None, [{'url': '/item/1', 'title': 'Good'}]])
def test_malformed_scoped_destination_preserves_valid_siblings(links):
    scope = {'html_selector': '.items', 'start_pattern': r'(?m)^# Items$', 'end_pattern': r'(?m)^# End$'}
    contract = record_task(entry_scope=scope)['acceptance_contract']
    text = '# Items\n[Good](/item/1)\n[Broken](https://[bad]/item/2)\n# End'
    entries = extract_named_entries(text, contract, 'https://shop.example/catalog', links=links)
    assert entries == [{'url': 'https://shop.example/item/1', 'title': 'Good'}]
    result = evaluate_response(task(acceptance_contract=contract), text.encode(), metadata={'links': links} if links else None)
    assert result['metrics']['stable_complete_record_count'] == 1
    assert result['metrics']['stable_task_errors'] == ['invalid_link_output']


@pytest.mark.parametrize('selector', ['.product', '.catalog .product'])
def test_selected_product_fields_retain_their_ancestor_context(selector):
    scope = {'html_selector': selector, 'title_selector': '.name',
             'link_selector': 'a.detail', 'price_selector': '.price'}
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'], entry_scope=scope)
    text = ('<div class="catalog"><div class="product"><h2 class="name">Widget</h2>'
            '<a class="detail" href="/item/1">Details</a><span class="price">$10.00</span></div></div>')
    assert evaluate_response(contract, text.encode())['passed']
    assert not evaluate_response(contract, text.replace('$10.00', '10 reviews').encode())['passed']


@pytest.mark.parametrize('suffix', [
    '[Shipping](/help?amount=$10.00)', '<span title="$10.00">Shipping</span>',
    '[Shipping](/help "Price $10.00")',
    '[Shipping](/help/unavailable)', '<span title="unavailable">Shipping</span>',
])
def test_product_price_and_unavailability_require_visible_text(suffix):
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'],
                           price_unavailable_patterns=['unavailable'])
    assert not evaluate_response(contract, ('[Widget](/item/1) ' + suffix).encode())['passed']
    assert evaluate_response(contract, ('[Widget](/item/1) $9.00 ' + suffix).encode())['passed']
    assert evaluate_response(contract, '[Widget](/item/1) unavailable'.encode())['passed']


@pytest.mark.parametrize('wrapper', ['<span hidden>{}</span>', '<div style="display:none">{}</div>',
                                   '<template>{}</template>', '<details><summary>More</summary>{}</details>'])
@pytest.mark.parametrize('frame', [False, True])
def test_image_answer_visibility_excludes_hidden_targets_and_noise(wrapper, frame):
    target = '<img src=/target.png>'
    hidden_target = wrapper.format(target)
    mixed = target + wrapper.format('<img src=/noise.png>')
    wrap = (lambda value: '<iframe srcdoc="' + html.escape(value, quote=True) + '"></iframe>') if frame else (lambda value: value)
    assert not evaluate_response(image_task(), wrap(hidden_target).encode())['passed']
    result = evaluate_response(image_task(), wrap(mixed).encode())
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == ['https://shop.example/target.png']
    assert result['metrics']['resource_precision'] == 1


def test_generic_resource_discovery_keeps_its_existing_hidden_url_policy():
    contract = image_task()
    contract.pop('resource_output_contract')
    assert evaluate_response(contract, b'<span hidden><img src=/target.png></span>')['passed']


def test_image_bare_scanning_does_not_reinterpret_any_html_attributes():
    url = 'https://example.test/target.png?a=1&b=2'
    contract = image_task()
    contract['expected_resources'] = [{'urls': [url]}]
    text = ('<img src="https://example.test/target.png?a=1&amp;b=2">'
            '<a href="https://example.test/target.png?a=1&amp;b=2">Download</a>'
            '<span title="https://example.test/noise.png">Plain text</span>')
    result = evaluate_response(contract, text.encode())
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == [url]
    assert result['metrics']['resource_precision'] == 1
    contract['expected_resources'] = [{'urls': [html.escape(url)]}]
    assert not evaluate_response(contract, text.encode())['passed']


def test_shared_dom_projection_preserves_supported_sections_and_skips_opaque_unknowns():
    assert visible_html_text('<![CDATA[Visible literal]]>') == 'Visible literal'
    assert visible_html_text('<p>Before</p><![UNRECOGNIZED[ opaque ]]><p>After</p>') == 'Before\nAfter'
