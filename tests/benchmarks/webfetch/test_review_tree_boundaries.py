"""Tree requirements survive acquisition and reject failed/malformed answers."""
import json

import pytest

from harness.web_fetch.config import load_dataset
from lexbench_webfetch.benchmark import public_task
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.scoring import score_attempt


REFERENCE = {'tag': 'table', 'children': [{'tag': 'td', 'text': 'Alpha'}]}


def task(**values):
    return {'id': 'tree', 'name': 'Tree', 'url': 'https://example.test/',
            'category': 'table', 'expected_any': [], 'ordered_tree_reference': REFERENCE, **values}


@pytest.mark.parametrize('threshold', [None, 0.9])
def test_loader_to_scorer_preserves_declared_tree_requirement(tmp_path, threshold):
    case = task(**({'ordered_tree_min_similarity': threshold} if threshold is not None else {}))
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps({'tool': 'web_fetch', 'cases': [case]}))
    _, loaded = load_dataset(path)
    assert loaded[0].ordered_tree_reference == REFERENCE
    result = score_attempt(loaded[0], json.dumps(REFERENCE).encode(), ok=True)
    assert result['ordered_tree_similarity'] == 1
    assert ('tree' in result['acceptance_checks']) is (threshold is not None)
    assert ('ordered_tree_min_similarity' in public_task(vars(loaded[0]))) is (threshold is not None)
    assert ('ordered_tree_min_similarity' in vars(loaded[0])) is (threshold is not None)


def test_ordinary_case_keeps_undeclared_tree_inputs_out_of_persisted_task():
    values = task()
    values.pop('ordered_tree_reference')
    case = EvalCase(**values)
    assert not any(key.startswith('ordered_tree_') for key in vars(case))
    result = score_attempt(case, b'Normal answer', ok=True)
    assert 'tree' not in result['acceptance_checks']


@pytest.mark.parametrize('answer', [b'', b'not JSON', b'[]', b'{"tag":"td","children":null}',
                                   b'{"tag":"td","text":[]}',
                                   (b'{"tag":"div","children":[' * 500 + b'{"tag":"div"}' + b']}' * 500)])
@pytest.mark.parametrize('ok', [False, True])
def test_failed_or_malformed_tree_answers_fail_without_aborting(answer, ok):
    result = evaluate_response(task(ordered_tree_min_similarity=1), answer, ok=ok)
    assert result['passed'] is False
    assert result['metrics']['acceptance_checks']['tree'] is False
    assert 'ordered_tree_similarity' not in result['metrics']
    assert result['metrics']['ordered_tree_error']


@pytest.mark.parametrize('reference,threshold', [([], 1), ({'tag': 'td', 'text': []}, 1),
                                                (REFERENCE, -0.1), (REFERENCE, 1.1),
                                                (REFERENCE, True), (REFERENCE, float('nan')),
                                                (None, 0.5)])
def test_invalid_tree_configuration_is_not_misclassified_as_an_answer_failure(reference, threshold):
    with pytest.raises(ValueError):
        score_attempt(task(ordered_tree_reference=reference, ordered_tree_min_similarity=threshold),
                      b'bad answer', ok=False)


def test_diagnostic_tree_similarity_does_not_create_a_pass_threshold():
    answer = {'tag': 'p', 'text': 'Different structure'}
    result = evaluate_response(task(), json.dumps(answer).encode())
    assert result['metrics']['ordered_tree_similarity'] < 1
    assert 'tree' not in result['metrics']['acceptance_checks']


def test_valid_tree_does_not_rescue_a_failed_request():
    result = evaluate_response(task(ordered_tree_min_similarity=1), json.dumps(REFERENCE).encode(), ok=False)
    assert result['passed'] is False
    assert result['metrics']['ordered_tree_pass'] is False


def test_overly_deep_reference_is_a_configuration_error():
    reference = {'tag': 'div'}
    for _ in range(1000):
        reference = {'tag': 'div', 'children': [reference]}
    with pytest.raises(ValueError, match='reference exceeds supported nesting'):
        score_attempt(task(ordered_tree_reference=reference), b'', ok=False)
