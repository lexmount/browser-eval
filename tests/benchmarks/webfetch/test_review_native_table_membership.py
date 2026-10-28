"""Protected native text cannot hide internal row-ownership sentinels."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response


CASE = {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [{
    'id': 'items', 'kind': 'named_link', 'minimum_records': 2, 'hosts': ['example.test'],
    'entry_path': r'/item/[0-9]+', 'required_fields': [{'id': 'description', 'kind': 'text', 'minimum_chars': 5}]}]}}


def artifact(described, decorated=True):
    rows = []
    for index in [1, 2]:
        name = f'Device-{index} 20%' if decorated else f'Device {index}'
        body = '<td>A detailed device description.</td>' if described else ''
        rows.append(f'<tr><td><a href="/item/{index}">{name}</a></td>{body}</tr>')
        rows.append('<tr><th>Threads</th><th>Posts</th><th>Last Post</th></tr>')
    return '<html><body><table>' + ''.join(rows) + '</table></body></html>'


@pytest.mark.parametrize('described', [False, True])
@pytest.mark.parametrize('decorated', [False, True])
@pytest.mark.parametrize('representation', [None, 'html'])
def test_header_rows_do_not_supply_another_rows_description(described, decorated, representation):
    result = evaluate_response(CASE, artifact(described, decorated).encode(),
                               metadata={'answer_format': representation} if representation else None)
    assert result['measured']
    assert result['passed'] is described
    if described and decorated:
        assert [r['title'] for r in result['metrics']['composite_group_results'][0]['records']] == ['Device-1 20%', 'Device-2 20%']


def scoring_controls():
    for described in [False, True]:
        yield {'id': 'native-row-membership-' + ('owned-description' if described else 'headers-only'),
               'category': 'structured-extraction', 'failure_mode': 'false-negative' if described else 'false-positive',
               'root_cause': 'Internal table-membership sentinels are compared after native literal decoding so only owning data cells supply fields.',
               'case': CASE, 'artifact': artifact(described), 'ok': True,
               'metadata': {'answer_format': 'html'}, 'expected_task_pass': described}
