"""Stable notices survive native text concatenation without accepting word fragments."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.mark.parametrize('prefix', ['', 'ALL RIGHTS RESERVED', 'TODOS LOS DERECHOS RESERVADOS'])
def test_complete_notice_after_an_adjacent_capitalized_label(prefix):
    notice = 'Example Corp. Avenida N°5, Santiago de Chile'
    case = {'url': 'https://example.test/', 'acceptance_contract': {
        'version': 1, 'schema': 'body_text', 'minimum_records': 0,
        'stable_text_anchors': [{'id': 'notice', 'alternatives': [notice]}]}}
    result = score_attempt(case, (prefix + notice).encode(), ok=True)
    assert task_passed(True, result)


@pytest.mark.parametrize('notice', [
    'Examples Corp. Avenida N°5, Santiago de Chile',
    'Example Corp. Avenida N°5, Santiago de Chilean',
    'Example Corp. Avenida N°6, Santiago de Chile',
])
def test_adjacent_notice_still_requires_the_complete_declared_words(notice):
    case = {'url': 'https://example.test/', 'acceptance_contract': {
        'version': 1, 'schema': 'body_text', 'minimum_records': 0,
        'stable_text_anchors': [{'id': 'notice', 'alternatives': [
            'Example Corp. Avenida N°5, Santiago de Chile']} ]}}
    result = score_attempt(case, ('ALL RIGHTS RESERVED' + notice).encode(), ok=True)
    assert not task_passed(True, result)


@pytest.mark.parametrize('gate', ['fact', 'anchor'])
@pytest.mark.parametrize('expected,output', [
    ('In stock', 'In stockAdd to basket'),
    ('Наука', '[Технологии](/technology)[Наука](/science)Все темы'),
    ('Careers', '[Careers9 of 9](/careers)'),
    ('toute l’actu dès 7h30', '_:_toute l’actu dès 7h30'),
])
def test_native_label_boundaries_are_preserved_for_facts_and_anchors(gate, expected, output):
    case = {'url': 'https://example.test/'}
    if gate == 'fact':
        case['required_facts'] = [{'id': 'label', 'alternatives': [expected]}]
    else:
        case['acceptance_contract'] = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'label', 'alternatives': [expected]}]}
    assert task_passed(True, score_attempt(case, output.encode(), ok=True))


@pytest.mark.parametrize('expected,output', [
    ('In stock', 'In stockholders'), ('Наука', '[Наукам](/science)'),
    ('Careers', '[Careers9](/careers)'), ('stats', 'statsmodels'),
])
def test_native_label_recovery_does_not_accept_a_different_word(expected, output):
    case = {'url': 'https://example.test/', 'required_facts': [
        {'id': 'label', 'alternatives': [expected]}]}
    assert not task_passed(True, score_attempt(case, output.encode(), ok=True))
