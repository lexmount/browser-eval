"""Adjacent schemas keep their own headers; excluded products never count."""
import hashlib

import pytest

from lexbench_webfetch.dynamic_lists import extract_linked_products, named_entry_source_pair_hash
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


TABLES = [
    ('product', '|name|price|\n|---|---|\n|Alpha|$10|\n',
     '|price|name|\n|---|---|\n|$20|Beta|', '|price|name|\n|---|---|\n|Beta|$20|'),
    ('review', '|date|text|\n|---|---|\n|2026-01-01|First review|\n',
     '|text|date|\n|---|---|\n|Second review|2026-02-01|',
     '|text|date|\n|---|---|\n|2026-02-01|Second review|'),
]


def table_controls():
    for schema, first, correct, wrong in TABLES:
        contract = {'version': 1, 'schema': schema, 'minimum_records': 2, 'maximum_records': 2}
        for kind, second, passed in [('reordered', correct, True), ('wrong-order', wrong, False)]:
            for separator in ['', '\n']:
                yield (f'{schema}-{kind}-{bool(separator)}', first + separator + second, contract, passed)


def product_controls():
    for representation in ['markdown', 'html', 'scoped-html']:
        contract = {'version': 1, 'schema': 'linked_product', 'minimum_records': 1, 'maximum_records': 1,
                    'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'record_name_exclusions': ['Generic'],
                    'price_patterns': [r'\$\d+'], 'price_exclusion_patterns': [], 'stable_text_anchors': []}
        if representation == 'scoped-html':
            contract['entry_scope'] = {'html_selector': '.product', 'title_selector': '.name',
                                       'link_selector': 'a[href]', 'price_selector': '.price'}
        def card(name, number):
            if representation == 'markdown':
                return f'[{name}](https://example.test/item/{number}) $10'
            return f'<div class="product"><a class="name" href="/item/{number}">{name}</a><span class="price">$10</span></div>'
        for label, names, passed in [('excluded', [('Generic', 1)], False),
                                     ('admitted', [('Specific', 2)], True),
                                     ('mixed', [('Generic', 1), ('Specific', 2)], True)]:
            text = '\n\n'.join(card(name, number) for name, number in names)
            yield f'{representation}-{label}', text, contract, passed, [name for name, _ in names if name == 'Generic']
        pair_hash = named_entry_source_pair_hash('Specific', 'https://example.test/item/2', contract)
        qualified = dict(contract, source_record_pair_hashes=[pair_hash],
                         source_record_pairs_sha256=hashlib.sha256(pair_hash.encode()).hexdigest())
        yield (f'{representation}-qualified', card('Generic', 1) + '\n\n' + card('Specific', 2),
               qualified, True, ['Generic'])
        for reverse in [False, True]:
            names = [('Generic', 1), ('Specific', 1)]
            if reverse:
                names.reverse()
            yield (f'{representation}-same-url-{reverse}', '\n\n'.join(card(name, number) for name, number in names),
                   contract, True, ['Generic'])


@pytest.mark.parametrize('identifier,text,contract,passed', list(table_controls()))
def test_adjacent_tables_are_parsed_with_independent_column_mappings(identifier, text, contract, passed):
    direct = evaluate_stable_task(text, contract)
    assert direct['stable_task_pass'] is passed
    if passed:
        assert len(direct['stable_records']) == 2
        assert all(row['valid'] for row in direct['stable_records'])
    assert evaluate_response({'url': 'https://example.test/', 'acceptance_contract': contract}, text.encode())['passed'] is passed


@pytest.mark.parametrize('identifier,text,contract,passed,excluded', list(product_controls()))
def test_linked_product_exclusion_preserves_audit_and_shared_admission(identifier, text, contract, passed, excluded):
    result = evaluate_response({'url': 'https://example.test/', 'acceptance_contract': contract}, text.encode())
    assert result['passed'] is passed
    assert result['metrics']['stable_excluded_record_names'] == excluded
    assert result['metrics']['stable_complete_record_count'] == int(passed)
    entries = extract_linked_products(text, contract, 'https://example.test/')
    assert [entry['title'] for entry in entries] == (['Specific'] if passed else [])


def scoring_controls():
    for identifier, text, contract, passed in table_controls():
        yield {'id': 'runtime12-' + identifier, 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'adjacent_generic_pipe_tables_own_their_column_mappings',
               'case': {'url': 'https://example.test/', 'acceptance_contract': contract},
               'artifact': text, 'ok': True, 'expected_task_pass': passed}
    for identifier, text, contract, passed, excluded in product_controls():
        yield {'id': 'runtime12-' + identifier, 'category': 'acceptance-contract',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'linked_product_exclusions_precede_admission_and_source_verification',
               'case': {'url': 'https://example.test/', 'acceptance_contract': contract},
               'artifact': text, 'ok': True, 'expected_task_pass': passed,
               'expected_metrics': {'stable_excluded_record_names': excluded,
                                    'stable_complete_record_count': int(passed)}}
