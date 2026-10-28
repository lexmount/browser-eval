"""Configured numeric limits use exact decimal values rather than binary rounding."""
from copy import deepcopy
import json

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from .test_review_rendered_anchor_tables import TABLE_CONTRACT


def task(kind, **bounds):
    contract = deepcopy(TABLE_CONTRACT)
    contract['table_columns'][1].update(type=kind, **bounds)
    contract['table_scope']['plain_row_pattern'] = r'(?P<name>[^\t\r\n]+)\t(?P<count>[^\t\r\n]+)'
    return {'url': 'https://example.test/', 'acceptance_contract': contract}


def artifact(value):
    return 'Name\tCount\nWidget\t' + value + '\nEnd'


def cases():
    for identifier, kind, bound_name, bound, at, outside in [
        ('integer-large-max', 'integer', 'maximum', 9007199254740992, '9007199254740992', '9007199254740993'),
        ('integer-large-min', 'integer', 'minimum', 9007199254740993, '9007199254740993', '9007199254740992'),
        ('integer-negative-max', 'integer', 'maximum', -9007199254740993, '-9007199254740993', '-9007199254740992'),
        ('integer-negative-min', 'integer', 'minimum', -9007199254740992, '-9007199254740992', '-9007199254740993'),
        ('decimal-max', 'number', 'maximum', 100, '100', '100.000000000000001'),
        ('decimal-min', 'number', 'minimum', 100, '100', '99.999999999999999'),
        ('decimal-negative-max', 'number', 'maximum', -100, '-100', '-99.999999999999999'),
        ('decimal-negative-min', 'number', 'minimum', -100, '-100', '-100.000000000000001'),
        ('float-bound-max', 'number', 'maximum', 0.1, '0.1', '0.100000000000000001'),
        ('float-bound-min', 'number', 'minimum', 0.1, '0.1', '0.099999999999999999'),
    ]:
        for suffix, value, passed in [('at', at, True), ('outside', outside, False)]:
            yield identifier + '-' + suffix, task(kind, **{bound_name: bound}), value, passed


@pytest.mark.parametrize('identifier,case,value,expected', list(cases()))
def test_numeric_limits_are_exact_in_public_scoring(identifier, case, value, expected):
    result = evaluate_response(case, artifact(value).encode(), metadata={'answer_format': 'plain_text'})
    assert result['measured']
    assert result['passed'] is expected
    json.dumps(result, allow_nan=False)
    if expected:
        assert result['metrics']['stable_records'][0]['fields']['count'] == value


@pytest.mark.parametrize('value,kind', [('000123', 'integer'), ('-0', 'integer'), ('00.10', 'number'),
    ('9' * 4500, 'integer')])
def test_existing_numeric_spelling_and_string_outputs_remain_supported(value, kind):
    result = evaluate_response(task(kind), artifact(value).encode(), metadata={'answer_format': 'plain_text'})
    assert result['passed']
    assert result['metrics']['stable_records'][0]['fields']['count'] == value
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('kind', ['integer', 'number'])
@pytest.mark.parametrize('name', ['minimum', 'maximum'])
@pytest.mark.parametrize('bound', [float('nan'), float('inf'), float('-inf'), True, '100', None])
def test_invalid_or_nonfinite_bounds_are_rejected_at_configuration_boundary(kind, name, bound):
    with pytest.raises(ValueError, match='bound'):
        evaluate_response(task(kind, **{name: bound}), artifact('100').encode())


@pytest.mark.parametrize('kind', ['integer', 'number'])
def test_inverted_bounds_remain_invalid(kind):
    with pytest.raises(ValueError, match='bound'):
        evaluate_response(task(kind, minimum=9007199254740993, maximum=9007199254740992), artifact('100').encode())


@pytest.mark.parametrize('kind', ['integer', 'number'])
@pytest.mark.parametrize('value', ['NaN', 'Infinity', '1e2', '+1', '1,000', '.1', '1.'])
def test_exact_comparison_does_not_expand_numeric_grammar(kind, value):
    assert not evaluate_response(task(kind), artifact(value).encode(), metadata={'answer_format': 'plain_text'})['passed']


@pytest.mark.parametrize('bound_name', ['minimum', 'maximum'])
def test_currency_bounds_keep_the_existing_rejected_contract(bound_name):
    with pytest.raises(ValueError, match='bound'):
        evaluate_response(task('currency_usd', **{bound_name: 100}), artifact('$100.00').encode())


def scoring_controls():
    selected = {'integer-large-max', 'integer-negative-min', 'decimal-max', 'decimal-negative-min', 'float-bound-min'}
    for identifier, case, value, expected in cases():
        if identifier.rsplit('-', 1)[0] in selected:
            yield {'id': 'numeric18-' + identifier, 'category': 'structured-extraction',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Declared numeric table bounds compare exact integer/decimal values without binary floating-point rounding.',
                   'case': case, 'artifact': artifact(value), 'metadata': {'answer_format': 'plain_text'},
                   'ok': True, 'expected_task_pass': expected}
