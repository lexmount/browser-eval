"""Native product fields belong to one explicit HTML card."""
from copy import deepcopy

import pytest

from lexbench_webfetch.metrics import task_passed, task_measured
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task


def task(schema, minimum=1):
    return {'url': 'https://shop.example/catalog', 'task_kind': 'body',
            'acceptance_contract': {'version': 1, 'schema': schema, 'minimum_records': minimum}}


def fields(schema, name='Alpha', price='$10'):
    suffix = '<p>Blue machine.</p><p>3 reviews</p>' if schema == 'catalog_product' else ''
    return '<div><h2>' + name + '</h2></div><section><p>' + price + '</p>' + suffix + '</section>'


def controls():
    for schema in ('product', 'catalog_product'):
        for tag, attrs in (('article', ''), ('li', ''), ('div', ' role="listitem"')):
            for complete in (False, True):
                if complete:
                    cards = f'<{tag}{attrs}>' + fields(schema) + f'</{tag}>'
                elif schema == 'product':
                    cards = f'<{tag}{attrs}><h2>Alpha</h2></{tag}><{tag}{attrs}><p>$10</p></{tag}>'
                else:
                    cards = (f'<{tag}{attrs}><h2>Alpha</h2><p>$10</p></{tag}>'
                             f'<{tag}{attrs}><p>Blue machine.</p><p>3 reviews</p></{tag}>')
                content = '<main><section><div>' + cards + '</div></section></main>'
                yield (f'{schema}-html-{tag}-' + ('complete' if complete else 'cross-card'),
                       content, task(schema), complete)
        for complete in (False, True):
            child = '<article><h2>Beta</h2><p>$20</p>'
            if schema == 'catalog_product':
                child += '<p>Red machine.</p><p>4 reviews</p>'
            child += '</article>'
            tail = '<p>$10</p>'
            if schema == 'catalog_product':
                tail += '<p>Blue machine.</p><p>3 reviews</p>'
            content = '<article><h2>Alpha</h2><aside>' + child + '</aside>' + (tail if complete else '') + '</article>'
            yield (f'{schema}-html-nested-' + ('complete' if complete else 'incomplete'),
                   content, task(schema, 2), complete)
        content = '<main><article><h2>Alpha</h2></article><section><p>$10</p></section></main>'
        if schema == 'catalog_product':
            content = content.replace('</section>', '<p>Blue machine.</p><p>3 reviews</p></section>')
        yield (f'{schema}-html-outside-price', content, task(schema), False)
        content = ('<article><h2>Alpha</h2><p>$10</p><ul><li>Blue machine.</li></ul><p>3 reviews</p></article>'
                   if schema == 'catalog_product' else '<article><div><h2>Alpha</h2></div><div><p>$10</p></div></article>')
        yield (f'{schema}-html-presentation-wrappers', content, task(schema), True)
        for complete in (False, True):
            cards = ('<div role="listitem">' + fields(schema) + '</div>' if complete else
                     '<div role="listitem"><h2>Alpha</h2></div><div role="listitem"><p>$10</p>'
                     + ('<p>Blue machine.</p><p>3 reviews</p>' if schema == 'catalog_product' else '')
                     + '</div>')
            yield (f'{schema}-html-nested-listitems-' + ('complete' if complete else 'cross-card'),
                   '<article>' + cards + '</article>', task(schema), complete)


@pytest.mark.parametrize('identifier, content, case, expected', list(controls()))
def test_product_fields_stay_inside_their_native_card(identifier, content, case, expected):
    direct = evaluate_stable_task(content, case['acceptance_contract'], case['url'])
    assert direct['stable_task_pass'] is expected
    metrics = score_attempt(deepcopy(case), content.encode(), ok=True)
    assert task_measured(metrics, 'body')
    assert task_passed(True, metrics, 'body') is expected


def scoring_controls():
    selected = {
        'product-html-article-cross-card', 'product-html-article-complete',
        'catalog_product-html-article-cross-card', 'catalog_product-html-article-complete',
        'product-html-nested-complete', 'catalog_product-html-presentation-wrappers',
    }
    for identifier, content, case, expected in controls():
        if identifier not in selected:
            continue
        yield {
            'id': identifier,
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'category': 'structured-extraction',
            'root_cause': 'Native product cards lost explicit ownership boundaries and borrowed prices, descriptions or review counts from neighboring cards.',
            'case': case, 'artifact': content, 'ok': True, 'expected_task_pass': expected,
        }
