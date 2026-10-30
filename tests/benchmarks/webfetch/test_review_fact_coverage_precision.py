"""Declared fact thresholds compare the unrounded hit fraction."""
from math import nextafter, inf

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


BOUNDARIES = [
    ('rounded-up', 2, 3, .6667, False),
    ('above-two-thirds', 2, 3, nextafter(2 / 3, inf), False),
    ('equal-two-thirds', 2, 3, 2 / 3, True),
    ('below-two-thirds', 2, 3, nextafter(2 / 3, -inf), True),
    ('rounded-down', 1, 3, .33332, True),
    ('display-equal', 1, 3, .3333, True),
    ('above-one-third', 1, 3, nextafter(1 / 3, inf), False),
    ('equal-one-third', 1, 3, 1 / 3, True),
    ('below-one-third', 1, 3, nextafter(1 / 3, -inf), True),
    ('no-hits', 0, 3, .1, False),
    ('complete', 3, 3, 1, True),
    ('no-facts', 0, 0, 1, True),
]


def scenario(hits, total, threshold, stable):
    labels = ['Alpha requirement verified', 'Beta requirement verified', 'Gamma requirement verified']
    case = {'url': 'https://example.test/manual', 'min_content_chars': 1,
            'required_facts': [{'id': f'fact-{index}', 'alternatives': [label]}
                               for index, label in enumerate(labels[:total])],
            'fact_coverage_threshold': threshold}
    if stable:
        case['acceptance_contract'] = {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'identity', 'alternatives': ['Client manual']}],
        }
    return case, 'Client manual\n' + '\n'.join(labels[:hits])


@pytest.mark.parametrize('stable', [False, True])
@pytest.mark.parametrize('identifier,hits,total,threshold,passed', BOUNDARIES)
def test_fact_fraction_thresholds_do_not_use_display_rounding(identifier, hits, total, threshold, passed, stable):
    case, text = scenario(hits, total, threshold, stable)
    result = score_attempt(case, text.encode(), ok=True)
    assert result['fact_hits'] == hits
    assert result['fact_total'] == total
    expected_coverage = round(hits / total, 4) if total else 1.0
    assert result['fact_coverage'] == expected_coverage
    assert [item['id'] for item in result['fact_results'] if not item['pass']] == [
        f'fact-{index}' for index in range(hits, total)]
    assert result['fact_coverage_pass'] is passed
    assert result['content_pass'] is passed
    assert task_passed(True, result) is passed
    if stable:
        assert result['stable_task_pass'] is passed
        assert result['stable_content_pass'] is passed
        assert ('required_facts_missing' in result['stable_task_errors']) is not passed


def scoring_controls():
    for stable in [False, True]:
        for identifier, hits, total, threshold, passed in BOUNDARIES:
            case, text = scenario(hits, total, threshold, stable)
            yield {'id': f'runtime28-facts-{identifier}-{stable}', 'category': 'reference-evidence',
                   'failure_mode': 'false-negative' if passed else 'false-positive',
                   'root_cause': 'fact_acceptance_uses_unrounded_fraction_not_reported_decimal',
                   'case': case, 'artifact': text, 'ok': True, 'expected_task_pass': passed,
                   'expected_metrics': {'fact_hits': hits, 'fact_total': total,
                                        'fact_coverage': round(hits / total, 4) if total else 1.0,
                                        'fact_coverage_pass': passed, 'content_pass': passed}}
