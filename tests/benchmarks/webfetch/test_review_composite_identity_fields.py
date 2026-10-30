"""Composite identities and field spans preserve distinct delivered values."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def case_for(group):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


def group_for(**extra):
    return {'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
            'hosts': ['example.test'], 'entry_path': r'/item/\d+', **extra}


def passed(case, artifact):
    return task_passed(True, score_attempt(case, artifact.encode(), ok=True), 'body')


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
@pytest.mark.parametrize('fields', [False, True])
def test_encoded_query_delimiters_do_not_merge_distinct_records(hosts, fields):
    group = group_for(hosts=hosts, minimum_records=2, identity_query_keys=['a', 'b'])
    if fields:
        group['required_fields'] = [{'id': 'price', 'kind': 'price'}]
    case = case_for(group)
    first = '[Record Alpha](/item/1?a=x%26b%3Dy&b=z)\n$12'
    second = '[Record Beta](/item/1?a=x&b=y%26b%3Dz)\n$13'
    assert passed(case, first + '\n\n' + second)
    assert not passed(case, first + '\n\n' + first)


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
@pytest.mark.parametrize('label', ['Read more', 'https://example.test/item/1', '/item/1'])
def test_host_policy_cannot_make_action_or_url_into_a_record_name(hosts, label):
    case = case_for(group_for(hosts=hosts))
    assert not passed(case, '[' + label + '](/item/1)')
    assert passed(case, '[Independent item name](/item/1)')


@pytest.mark.parametrize('prefix', ['Summary: ', 'Summary： ', '   Summary:   '])
def test_patterned_role_payload_cannot_supply_another_text_field(prefix):
    case = case_for(group_for(required_fields=[
        {'id': 'summary', 'kind': 'text', 'pattern': 'Alpha'},
        {'id': 'description', 'kind': 'text'}]))
    artifact = '[Independent item name](/item/1)\n' + prefix + 'Alpha'
    assert not passed(case, artifact)
    assert passed(case, artifact + '\n\nIndependent explanation')


@pytest.mark.parametrize('prefix', ['Author: ', 'Paper / by '])
def test_author_payload_remains_owned_after_consumption(prefix):
    case = case_for(group_for(required_fields=[
        {'id': 'author', 'kind': 'text', 'pattern': 'Alpha'},
        {'id': 'description', 'kind': 'text'}]))
    artifact = '[Independent item name](/item/1)\n' + prefix + 'Alpha'
    assert not passed(case, artifact)
    assert passed(case, artifact + '\n\nIndependent explanation')
