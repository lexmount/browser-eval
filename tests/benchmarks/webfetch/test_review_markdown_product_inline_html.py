"""Markdown link labels retain inline HTML without escaping record ownership."""
from copy import deepcopy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import source_record_hash


CASE = {'url': 'https://shop.example/catalog', 'expected_any': [],
        'acceptance_contract': {'version': 1, 'schema': 'linked_product', 'minimum_records': 1,
                                'hosts': ['shop.example'], 'entry_path': r'/product/\d+',
                                'stable_text_anchors': [], 'price_patterns': [r'\$\d+\.\d{2}']}}


def check(source, representation='markdown', case=None):
    case = case or CASE
    metadata = {} if representation is None else {'answer_format': representation}
    metrics = score_attempt(case, source.encode(), ok=True, metadata=metadata)
    return task_passed(True, metrics), metrics


def bound_case():
    case = deepcopy(CASE)
    fields = {'title': 'Widget', 'price': '$10.00', 'url': 'https://shop.example/product/1'}
    names = ['price', 'title', 'url']
    case['acceptance_contract']['source_membership'] = {
        'kind': 'fixed', 'fields': names, 'record_hashes': [source_record_hash(fields, names, 'linked_product')]}
    return case


@pytest.mark.parametrize('label', ['Widget', '<span>Widget</span>', '<strong>Widget</strong>',
                                  '<em><span title="a > b">Widget</span></em>',
                                  '<span>Widget</span> Pro'])
@pytest.mark.parametrize('representation', [None, 'markdown', 'html'])
@pytest.mark.parametrize('container', ['', '<div class="product">{}</div>'])
def test_markdown_labels_cross_inline_nodes_only_in_markdown_answers(label, representation, container):
    source = '[' + label + '](/product/1) $10.00'
    source = container.format(source) if container else source
    passed, metrics = check(source, representation)
    assert passed is (representation != 'html')
    if passed:
        expected = 'Widget Pro' if label.endswith(' Pro') else 'Widget'
        assert metrics['stable_records'][0]['fields']['title'] == expected
        assert metrics['stable_records'][0]['fields']['price'] == '$10.00'


@pytest.mark.parametrize('source', [
    '`[<span>Widget</span>](/product/1) $10.00`',
    '```\n[<span>Widget</span>](/product/1) $10.00\n```',
    '<pre>[<span>Widget</span>](/product/1) $10.00</pre>',
    '<code>[<span>Widget</span>](/product/1) $10.00</code>',
    '<div title="[<span>Widget</span>](/product/1) $10.00"></div>',
    '<!-- [<span>Widget</span>](/product/1) $10.00 -->',
    '<div hidden>[<span>Widget</span>](/product/1) $10.00</div>',
    '[<span>Widget</span>](/outside/1) $10.00',
    '[<span>Widget</span>](/product/1)<section>$10.00</section>',
    '<article>[<span>Widget</span>](/product/1)</article><article>$10.00</article>',
])
def test_literal_attributes_hidden_and_neighbor_sections_cannot_supply_products(source):
    assert not check(source)[0]


def test_formatted_markdown_and_native_html_products_remain_separate_records():
    case = deepcopy(CASE)
    case['acceptance_contract']['minimum_records'] = 2
    first = '<article><a href="/product/1">First</a><p>$10.00</p></article>'
    second = '<article>[<span>Second</span>](/product/2) $20.00</article>'
    assert check(first + second, case=case)[0]
    assert not check(first + second.replace(' $20.00', ''), case=case)[0]
    assert not check(first + second, 'html', case)[0]


@pytest.mark.parametrize('matched', [False, True])
def test_inline_label_preserves_declared_dom_scope(matched):
    case = deepcopy(CASE)
    case['acceptance_contract']['entry_scope'] = {'html_selector': '.product', 'price_selector': '.price'}
    source = '<div class="' + ('product' if matched else 'other') + '">\n[<span>Widget</span>](/product/1)\n<p class="price">$10.00</p></div>'
    assert check(source, case=case)[0] is matched


@pytest.mark.parametrize('label', ['Widget', '<span>Widget</span>', '<strong>Widget</strong>'])
@pytest.mark.parametrize('separator', ['\n', '\r\n', '\u2028'])
@pytest.mark.parametrize('representation', [None, 'markdown'])
def test_markdown_price_line_keeps_following_description(label, separator, representation):
    source = separator.join(['[' + label + '](/product/1)', '$10.00', 'Description'])
    passed, metrics = check(source, representation)
    assert passed
    assert metrics['stable_records'][0]['fields']['price'] == '$10.00'


def test_multiline_markdown_fields_do_not_change_native_html_or_section_ownership():
    first = '[<span>Widget</span>](/product/1)'
    assert not check(first + '\n$10.00\nDescription', 'html')[0]
    assert not check(first + '\n<section><h2>Shipping</h2><p>$10.00</p></section>')[0]
    case = deepcopy(CASE)
    case['acceptance_contract']['minimum_records'] = 2
    second = '<article>[<span>Other</span>](/product/2)\n$20.00\nDescription</article>'
    assert not check('<article>' + first + '</article>' + second, case=case)[0]
    assert check('<article>' + first + '\n$10.00\nDescription</article>' + second, case=case)[0]


@pytest.mark.parametrize('first_markdown', [False, True])
@pytest.mark.parametrize('correct_first', [False, True])
def test_conflicting_duplicates_keep_the_first_title_and_price(first_markdown, correct_first):
    case = bound_case()
    first, last = ('Widget', 'Wrong') if correct_first else ('Wrong', 'Widget')
    def record(title, markdown):
        return ('[<span>' + title + '</span>](/product/1)' if markdown
                else '<a href="/product/1">' + title + '</a>') + ' $10.00'
    source = record(first, first_markdown) + '\n' + record(last, not first_markdown)
    passed, metrics = check(source, case=case)
    assert passed is correct_first
    assert metrics['stable_records'][0]['fields']['title'] == first
    assert metrics['stable_records'][0]['fields']['price'] == '$10.00'


@pytest.mark.parametrize('entity_encoded', [False, True])
def test_user_supplied_placeholder_spelling_keeps_its_literal_title(entity_encoded):
    literal = '\ue028product-label0\ue029'
    supplied = '&#57384;product-label0&#57385;' if entity_encoded else literal
    source = '[<span>Widget</span>](/product/1) $10.00\n<a href="/product/2">' + supplied + '</a> $20.00'
    passed, metrics = check(source)
    assert passed
    assert [row['fields']['title'] for row in metrics['stable_records']] == ['Widget', literal]


def controls():
    sources = [
        ('formatted-markdown', '[<span>Widget</span>](/product/1) $10.00', 'markdown', True),
        ('same-bytes-html', '[<span>Widget</span>](/product/1) $10.00', 'html', False),
        ('price-line-description', '[<span>Widget</span>](/product/1)\n$10.00\nDescription', 'markdown', True),
        ('description-section-price', '[<span>Widget</span>](/product/1)\n<section><h2>Shipping</h2><p>$10.00</p></section>', 'markdown', False),
    ]
    rows = [{'id': 'markdown-product-inline-html-' + name, 'category': 'structured-extraction',
             'root_cause': 'Parsing HTML before Markdown link ownership split inline-formatted labels across DOM text nodes.',
             'failure_mode': 'false-negative' if expected else 'false-positive',
             'case': deepcopy(CASE), 'artifact': source, 'ok': True,
             'metadata': {'answer_format': representation}, 'expected_task_pass': expected}
            for name, source, representation, expected in sources]
    for correct_first in (False, True):
        case = bound_case()
        first, last = ('Widget', 'Wrong') if correct_first else ('Wrong', 'Widget')
        rows.append({**deepcopy(rows[0]), 'id': 'markdown-product-inline-html-first-label-' + str(correct_first).lower(),
                     'case': case, 'artifact': '[<span>' + first + '</span>](/product/1) $10.00\n<a href="/product/1">' + last + '</a> $10.00',
                     'expected_task_pass': correct_first,
                     'failure_mode': 'false-negative' if correct_first else 'false-positive'})
    neighbor_case = deepcopy(CASE)
    neighbor_case['acceptance_contract']['minimum_records'] = 2
    rows.append({**deepcopy(rows[0]), 'id': 'markdown-product-inline-html-description-neighbor-card',
                 'case': neighbor_case,
                 'artifact': '<article>[<span>Widget</span>](/product/1)</article><article>[<span>Other</span>](/product/2)\n$20.00\nDescription</article>',
                 'expected_task_pass': False, 'failure_mode': 'false-positive'})
    for row in rows:
        if 'description' in row['id']:
            row['root_cause'] = 'Markdown-owned product text lines were treated as one DOM scalar after inline HTML selected card parsing.'
    return rows


CONTROLS = controls()
