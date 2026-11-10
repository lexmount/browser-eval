"""Scope revisions may revise the request text without changing retrieval inputs."""
from copy import deepcopy

import pytest

from lexbench_webfetch.annotation_revisions import apply_annotation_change


def test_prompt_revision_requires_explicit_scope_authority():
    case = {'id': 'article', 'url': 'https://example.test/', 'task_prompt': 'Read the page'}
    original = deepcopy(case)
    change = {'set': {'task_prompt': 'Read the article and retain its author'}}
    with pytest.raises(ValueError, match='cannot change collection inputs'):
        apply_annotation_change(case, change, None)
    assert case == original
    apply_annotation_change(case, change, None, task_scope_revision=True)
    assert case == {**original, **change['set']}


@pytest.mark.parametrize('field', ['id', 'url', 'track', 'task_kind', 'retrieval_hints'])
def test_scope_revision_cannot_change_identity_or_retrieval(field):
    with pytest.raises(ValueError, match='cannot change collection inputs'):
        apply_annotation_change({}, {'set': {field: 'replacement'}}, None, task_scope_revision=True)


@pytest.mark.parametrize('change', [{'remove': ['task_prompt']}, {'set': {'task_prompt': ''}},
                                   {'set': {'task_prompt': ' '}}, {'set': {'task_prompt': None}}])
def test_scope_revision_cannot_remove_or_empty_the_request(change):
    with pytest.raises(ValueError, match='nonempty task prompt'):
        apply_annotation_change({}, change, None, task_scope_revision=True)
