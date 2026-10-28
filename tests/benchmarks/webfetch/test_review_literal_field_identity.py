"""Already-extracted fields bind literal identities and literal structured anchors."""
import hashlib
from html import escape

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task, source_record_hash


def _case(contract):
    return {'url': 'https://example.test/', 'acceptance_contract': contract}


def membership_contract(name='Widget'):
    fields = ['name', 'price']
    return {'version': 1, 'schema': 'product', 'minimum_records': 1,
            'source_membership': {'kind': 'fixed', 'fields': fields,
                                  'record_hashes': [source_record_hash({'name': name, 'price': '$10.00'}, fields, 'product')]}}


MEMBERSHIP = []
for representation, text, passed in [
    ('html', '<article><p>Widget</p><p>$10.00</p></article>', True),
    ('html', '<article><p><b>Widget</b></p><p>$10.00</p></article>', True),
    ('html', '<article><p>**Widget**</p><p>$10.00</p></article>', False),
    ('html', '<article><p>[Widget](/other)</p><p>$10.00</p></article>', False),
    ('plain_text', '**Widget**\n$10.00', False),
    ('plain_text', '[Widget](/other)\n$10.00', False),
    ('markdown', '**Widget**\n$10.00', True),
    ('markdown', '[Widget](/other)\n$10.00', True),
]:
    MEMBERSHIP.append((f'membership-{len(MEMBERSHIP)}', text, membership_contract(), representation, passed))
for source_name in ['**Widget**', '[Widget](/other)']:
    for actual, passed in [(source_name, True), ('Widget', False)]:
        text = '<article><p>' + escape(actual) + '</p><p>$10.00</p></article>'
        MEMBERSHIP.append((f'literal-binding-{len(MEMBERSHIP)}', text, membership_contract(source_name), 'html', passed))


ANCHORS = []
for literal in ['1. Widget', '# Widget', '- Widget', '**Widget**', '[Widget](/other)']:
    for representation in ['plain_text', 'html']:
        text = literal + '\n$10.00' if representation == 'plain_text' else '<article><p>' + escape(literal) + '</p><p>$10.00</p></article>'
        for reference, passed in [(literal, True), ('Different Widget', False)]:
            contract = {'version': 1, 'schema': 'product', 'minimum_records': 1,
                        'stable_text_anchors': [{'id': 'exact-record', 'alternatives': [reference + '\n$10.00']}]}
            ANCHORS.append((f'anchor-{len(ANCHORS)}', text, contract, representation, passed))
for reference, passed in [('1. Widget', False), ('# Widget', False), ('**Widget**', False), ('[Widget](/other)', False)]:
    contract = {'version': 1, 'schema': 'product', 'minimum_records': 1,
                'stable_text_anchors': [{'id': 'literal-reference', 'alternatives': [reference + '\n$10.00']}]}
    ANCHORS.append((f'wrong-reference-{len(ANCHORS)}', 'Widget\n$10.00', contract, 'plain_text', passed))
for text, reference, representation in [
    ('1. Widget\n$10.00', '<article><p>1. Widget</p><p>$10.00</p></article>', 'plain_text'),
    ('<article><p>Widget</p><p>$10.00</p></article>', '|name|price|\n|---|---|\n|Widget|$10.00|', 'html'),
    ('<article><p><b>Widget</b></p><p>$10.00</p></article>', 'Widget\n$10.00', 'html'),
    ('**Widget**\n$10.00', 'Widget\n$10.00', 'markdown'),
]:
    contract = {'version': 1, 'schema': 'product', 'minimum_records': 1,
                'stable_text_anchors': [{'id': 'record-reference', 'alternatives': [reference]}]}
    ANCHORS.append((f'reference-layout-{len(ANCHORS)}', text, contract, representation, True))


@pytest.mark.parametrize('identifier,text,contract,representation,passed', MEMBERSHIP + ANCHORS)
def test_literal_source_membership_and_anchor_fields(identifier, text, contract, representation, passed):
    assert evaluate_stable_task(text, contract, representation=representation)['stable_task_pass'] is passed
    assert evaluate_response(_case(contract), text.encode(), metadata={'answer_format': representation})['passed'] is passed


@pytest.mark.parametrize('name,canonical', [('Widget', 'widget'), ('WIDGET', 'widget'),
                                          ('Widget’s – choice', "widget's - choice")])
def test_canonical_frozen_hash_serialization_and_typography_are_unchanged(name, canonical):
    import json
    expected = hashlib.sha256(json.dumps([canonical, '$10.00'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert source_record_hash({'name': name, 'price': '$10.00'}, ['name', 'price'], 'product') == expected


def scoring_controls():
    for identifier, text, contract, representation, passed in MEMBERSHIP + ANCHORS:
        yield {'id': 'runtime16-' + identifier, 'category': 'reference-evidence',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'already_extracted_field_identity_and_anchor_parsing_are_literal',
               'case': _case(contract), 'artifact': text, 'metadata': {'answer_format': representation},
               'ok': True, 'expected_task_pass': passed}
