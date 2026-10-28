"""HTML product fields, typed image answers and visible code keep their owners."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task, task
from tests.benchmarks.webfetch.test_review_owned_surfaces import image_task


@pytest.mark.parametrize('html,passed', [
    ('<div><a href="/item/1">Widget</a> $10.00</div>', True),
    ('<div><a href="/item/1">Widget</a></div><div>Shipping costs $10.00</div>', False),
    ('<main><div><a href="/item/1">Widget</a></div><div>Shipping costs $10.00</div></main>', False),
    ('<div><div><a href="/item/1">Widget</a></div><div><span>$10.00</span></div></div>', True),
    ('<div><div><a href="/item/1">Widget</a></div><div>Shipping costs <span>$10.00</span></div></div>', False),
    ('<a href="/item/1"><h3>Widget</h3><span>$10.00</span></a>', True),
    ('<a href="/item/1">Widget $10.00</a>', True),
    ('<div><a href="/item/1">Widget</a><span>$10.00</span><a href="/item/2">Other</a><span>$20.00</span></div>', True),
    ('<div><a href="/item/1">Widget</a><a href="/item/2">Details</a><span>$20.00</span></div>', False),
    ('<div>[Widget](/item/1)</div><div>Shipping costs $10.00</div>', False),
    ('<div><div>[Widget](/item/1)</div><div>$10.00</div></div>', True),
])
def test_html_fallback_uses_one_record_owner_and_keeps_presentation_wrappers(html, passed):
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    assert evaluate_response(contract, html.encode())['passed'] is passed


def test_unavailability_cannot_come_from_a_sibling_container():
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'],
                           price_unavailable_patterns=['Unavailable'])
    good = '<div><div><a href="/item/1">Widget</a></div><span>Unavailable</span></div>'
    bad = '<div><a href="/item/1">Widget</a></div><div>Unavailable</div>'
    assert evaluate_response(contract, good.encode())['passed']
    assert not evaluate_response(contract, bad.encode())['passed']


@pytest.mark.parametrize('branch', ['network', 'request', 'requests', 'logs', 'debug', 'diagnostics', 'DIAGNOSTICS'])
def test_image_metadata_never_promotes_diagnostic_branches(branch):
    contract = image_task()
    diagnostic_only = {'extras': {branch: {'images': ['/target.png']}}}
    assert not evaluate_response(contract, b'', metadata=diagnostic_only)['passed']
    actual_answer = {'images': ['/target.png'], 'extras': {branch: {'images': ['/noise.png']}}}
    result = evaluate_response(contract, b'', metadata=actual_answer)
    assert result['passed']
    assert result['metrics']['resource_candidate_urls'] == ['https://shop.example/target.png']
    assert result['metrics']['resource_precision'] == 1


@pytest.mark.parametrize('answer,passed', [
    ('<p>Visible introduction</p><pre>required_code()</pre>', True),
    ('<p>Visible introduction</p><pre hidden>required_code()</pre>', False),
    ('<p>Visible introduction</p><span hidden><pre>required_code()</pre></span>', False),
    ('<p>Visible introduction</p><div style="display:none"><pre>required_code()</pre></div>', False),
    ('`<pre hidden>required_code()</pre>`', True),
    ('```html\n<pre hidden>required_code()</pre>\n```', True),
    ('<p>Visible introduction</p><pre hidden>\n```\nrequired_code()\n```\n</pre>', False),
    ('<p>Visible introduction</p><pre title="required_code()">other_code()</pre>', False),
])
def test_compact_code_uses_visible_native_blocks_and_preserves_literal_examples(answer, passed):
    contract = task(required_patterns=[{'id': 'program', 'pattern': r'required_code\(\)',
                                       'minimum': 1, 'scope': 'code_compact'}])
    assert evaluate_response(contract, answer.encode())['passed'] is passed
