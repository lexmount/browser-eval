"""Ordered endings reject scattered or changed closings without freezing layout."""
from copy import deepcopy

import pytest

from lexbench_webfetch.body_coverage import (
    _ordered_ending_similarity, body_requirements, evaluate_body_requirements,
)
from lexbench_webfetch import annotation_revisions
from lexbench_webfetch import benchmark as bench

OPENING = 'A telescope observes distant galaxies and documents stellar motion. ' * 20
ENDING = 'alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima'


def contract(version=5):
    rule = body_requirements(OPENING + ENDING, structure_hint='whole_declared_component')
    rule.update(version=version, ending={'text': ENDING}, minimum_floor=.8,
                minimum_region_passes=1)
    return rule


@pytest.mark.parametrize('closing', [ENDING, '**' + ENDING + '**',
    ENDING.replace('foxtrot golf', 'foxtrot additional golf'),
    ENDING.replace(' ', '\n> '), ENDING.upper()])
def test_complete_formatted_and_locally_expanded_endings_pass(closing):
    result = evaluate_body_requirements(OPENING + closing + '\nPage footer', contract())
    assert result['body_coverage_pass']
    assert result['body_ending_result']['ordered_match']['pass']


def test_scattered_ending_fragments_no_longer_form_a_closing_passage():
    words = ENDING.split()
    chunks = [' '.join(words[i:i + 4]) for i in range(0, len(words), 4)]
    unrelated = ' A separate section describes a river flowing through mountains. ' * 4
    content = OPENING + unrelated.join(chunks)
    assert evaluate_body_requirements(content, contract(4))['body_coverage_pass']
    result = evaluate_body_requirements(content, contract())
    assert not result['body_coverage_pass']
    ending = result['body_ending_result']
    assert ending['coverage'] >= .8  # Legacy fragments still pass.
    assert not ending['ordered_match']['pass']


def test_music_in_the_intro_is_not_the_outro():
    result = _ordered_ending_similarity('[intro music plays] A discussion [music plays]',
                                        '[outro music plays]')
    assert result['edit_distance'] == 1
    assert result['similarity'] < .8


def test_local_edit_budget_and_free_surrounding_page_text():
    ending = 'alpha bravo charlie delta echo'
    for middle, expected in [('alpha bravo inserted charlie delta echo', True),
                             ('alpha bravo inserted extra charlie delta echo', False),
                             ('alpha bravo charlie echo', True),
                             ('echo delta charlie bravo alpha', False)]:
        result = _ordered_ending_similarity('site menu ' + middle + ' footer', ending)
        assert (result['similarity'] >= .8) is expected


def test_cjk_ending_retains_character_order():
    assert _ordered_ending_similarity('前言。这是最后结论。页脚', '这是最后结论')['similarity'] == 1
    assert _ordered_ending_similarity('论结后最是这', '这是最后结论')['similarity'] < .8


@pytest.mark.parametrize('ending', [None, {}, {'text': ''}, {'text': 4}])
def test_ordered_contract_rejects_invalid_ending(ending):
    rule = contract()
    rule['ending'] = ending
    with pytest.raises(ValueError):
        evaluate_body_requirements(OPENING, rule)


def test_old_contract_does_not_acquire_ordered_semantics_or_mutate():
    rule = contract(4)
    before = deepcopy(rule)
    result = evaluate_body_requirements(OPENING + ENDING, rule)
    assert 'ordered_match' not in result['body_ending_result']
    assert rule == before


def test_annotation_preserves_public_and_executable_reference_equivalence():
    executable = {'body_requirements': contract(4)}
    original = deepcopy(executable['body_requirements'])
    public = bench.public_task(executable)
    annotation_revisions._revise_body_ending_order(executable, True)
    annotation_revisions._revise_body_ending_order(public, True)
    assert bench.public_task(executable) == public
    original['version'] = 5
    assert executable['body_requirements'] == original


@pytest.mark.parametrize('enabled', [False, None, 1, 'true', {}])
def test_annotation_requires_explicit_true(enabled):
    case = {'body_requirements': contract(4)}
    before = deepcopy(case)
    with pytest.raises(ValueError):
        annotation_revisions._revise_body_ending_order(case, enabled)
    assert case == before


@pytest.mark.parametrize('replacement', [
    {'version': 3}, {'ending': {'text': ''}}, {'ending': {'text_binding': {}}},
    {'ending': {'unrecognized': True}}, {'minimum_region_passes': 0},
    {'minimum_floor': .6}, {'regions': []},
])
def test_annotation_cannot_upgrade_invalid_or_non_strict_bodies(replacement):
    rule = contract(4)
    rule.update(replacement)
    case = {'body_requirements': rule}
    before = deepcopy(case)
    with pytest.raises(ValueError):
        annotation_revisions._revise_body_ending_order(case, True)
    assert case == before


def test_registered_revision_changes_only_the_95_fixed_body_versions():
    _, root = bench.suite('articles')
    original = bench.read(root / 'tasks.json')
    snapshot = deepcopy(original)
    definition = bench.read(bench.SUITES / 'annotation-revisions' / 'review-550-20261023.json')
    operations = {r['case_id']: r['body_ending_order'] for r in definition['changes']
                  if r['suite'] == 'articles' and 'body_ending_order' in r}
    revised = deepcopy(original)
    for case in revised['cases']:
        if case['id'] in operations:
            annotation_revisions._revise_body_ending_order(case, operations[case['id']])
    assert original == snapshot
    changed = 0
    for old, new in zip(original['cases'], revised['cases']):
        expected = deepcopy(old)
        if expected.get('body_requirements'):
            assert expected['body_requirements']['version'] == 4
            expected['body_requirements']['version'] = 5
            changed += 1
        assert new == expected
    assert changed == 95


def test_annotation_dispatch_respects_dependencies_not_json_key_order():
    case = {'body_requirements': contract(4)}
    body = case['body_requirements']
    body['minimum_floor'] = .5
    body['minimum_region_passes'] = 1
    change = {'body_ending_order': True, 'body_thresholds': {
        'minimum_coverage': .8, 'minimum_floor': .8,
        'minimum_region_passes': len(body['regions']),
    }}
    original = deepcopy(change)
    annotation_revisions.apply_annotation_change(case, change, None)
    assert body['version'] == 5
    assert body['minimum_floor'] == body['minimum_coverage'] == .8
    assert body['minimum_region_passes'] == len(body['regions'])
    assert change == original
