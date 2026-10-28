"""Quota revisions preserve reference bytes and reject a single weak region."""
import copy
import pytest

from lexbench_webfetch import annotation_revisions
from lexbench_webfetch import benchmark as bench
from lexbench_webfetch.body_coverage import body_requirements, evaluate_body_requirements


def body():
    rule = body_requirements('The first complete section describes apple cultivation. ' * 12,
                             structure_hint='whole_declared_component')
    rule['regions'].append({'id': 'second', 'text': 'Z' * 105, 'source_text': 'Z' * 105})
    rule['minimum_region_passes'] = 1
    return rule


def test_threshold_revision_preserves_text_and_changes_only_weak_region_verdict():
    case = {'body_requirements': body()}
    original = copy.deepcopy(case)
    thresholds = dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=2)
    annotation_revisions._revise_body_thresholds(case, thresholds)
    assert case['body_requirements']['regions'] == original['body_requirements']['regions']
    first = original['body_requirements']['regions'][0]['text']
    # 105 characters yield 100 fragments; 75 yield 70, 85 yield 80.
    for size, expected in [(75, False), (84, False), (85, True), (105, True)]:
        answer = first + '\n' + 'Z' * size
        assert evaluate_body_requirements(answer, original['body_requirements'])['body_coverage_pass']
        assert evaluate_body_requirements(answer, case['body_requirements'])['body_coverage_pass'] is expected


@pytest.mark.parametrize('update', [
    {}, {'minimum_floor': .8},
    dict(minimum_coverage=.8, minimum_floor=.9, minimum_region_passes=2),
    dict(minimum_coverage=float('nan'), minimum_floor=.8, minimum_region_passes=2),
    dict(minimum_coverage=1.1, minimum_floor=.8, minimum_region_passes=2),
    dict(minimum_coverage=True, minimum_floor=.8, minimum_region_passes=2),
    dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=True),
    dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=0),
    dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=3),
    dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=2, regions=[]),
])
def test_bad_quotas_cannot_rewrite_a_reference(update):
    case = {'body_requirements': body()}
    before = copy.deepcopy(case)
    with pytest.raises(ValueError, match='body thresholds'):
        annotation_revisions._revise_body_thresholds(case, update)
    assert case == before


@pytest.mark.parametrize('rule', [None, {}, {'version': 1, 'regions': [{}]},
                                {'version': 3, 'regions': []}])
def test_quota_revision_requires_an_existing_compatible_body(rule):
    with pytest.raises(ValueError, match='body thresholds'):
        annotation_revisions._revise_body_thresholds({'body_requirements': rule},
            dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=1))


def test_strict_ending_contract_cannot_be_relaxed_to_one_weak_region():
    rule = body()
    rule.update(version=4, ending={'text': 'A required closing paragraph'})
    with pytest.raises(ValueError, match='body thresholds'):
        annotation_revisions._revise_body_thresholds({'body_requirements': rule},
            dict(minimum_coverage=.8, minimum_floor=.6, minimum_region_passes=1))


def test_same_revision_preserves_public_bindings_and_executable_references():
    executable = {'body_requirements': body()}
    public = bench.public_task(executable)
    original = copy.deepcopy(executable['body_requirements']['regions'])
    thresholds = dict(minimum_coverage=.8, minimum_floor=.8, minimum_region_passes=2)
    annotation_revisions._revise_body_thresholds(public, thresholds)
    annotation_revisions._revise_body_thresholds(executable, thresholds)
    assert bench.public_task(executable) == public
    assert executable['body_requirements']['regions'] == original


def test_published_revision_covers_all_legacy_bodies_and_keeps_bindings():
    revision = 'review-550-20261023'
    counts = {}
    for identifier in ('webfetch', 'dynamic-content', 'access-restrictions'):
        _, root = bench.suite(identifier)
        data = bench.read(root / 'tasks.json')
        before = copy.deepcopy(data)
        definition = bench.read(bench.SUITES / 'annotation-revisions' / (revision + '.json'))
        operations = {r['case_id']: r['body_thresholds'] for r in definition['changes']
                      if r['suite'] == identifier and 'body_thresholds' in r}
        revised = copy.deepcopy(data)
        revised['id'] += '--' + revision
        for case in revised['cases']:
            if case['id'] in operations:
                annotation_revisions._revise_body_thresholds(case, operations[case['id']])
        assert data == before
        assert revised['id'] == data['id'] + '--' + revision
        count = 0
        for old, new in zip(data['cases'], revised['cases']):
            if not old.get('body_requirements'):
                assert old == new
                continue
            count += 1
            old_body = old['body_requirements']
            new_body = new['body_requirements']
            assert new_body['regions'] == old_body['regions']
            assert new_body['minimum_floor'] == new_body['minimum_coverage'] == .8
            assert new_body['minimum_region_passes'] == len(new_body['regions'])
            restored = copy.deepcopy(new)
            restored['body_requirements'] = old_body
            assert restored == old
        counts[identifier] = count
    assert counts == {'webfetch': 27, 'dynamic-content': 42, 'access-restrictions': 79}
