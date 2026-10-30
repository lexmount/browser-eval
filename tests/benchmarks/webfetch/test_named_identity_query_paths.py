"""Mixed detail routes share one identity rule without weakening required query IDs."""
import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.stable_tasks import validate_acceptance_contract


def rules(host='library.example'):
    return {'version': 1, 'schema': 'named_link', 'minimum_records': 2,
            'hosts': [host], 'entry_path': r'(?:/detail|/article/[0-9]+)',
            'identity_query_keys': ['id'], 'identity_query_paths': ['/detail']}


def passes(contract, text):
    case = {'url': 'https://' + contract['hosts'][0] + '/', 'acceptance_contract': contract}
    return task_passed(True, score_attempt(case, text.encode(), ok=True))


@pytest.mark.parametrize('host', ['library.example', 'films.example'])
@pytest.mark.parametrize('text,expected', [
    ('[First record](/detail?id=7)\n[Second record](/article/8)', True),
    ('[First record](/detail)\n[Second record](/article/8)', False),
    ('[First record](/detail?id=)\n[Second record](/article/8)', False),
    ('[First record](/detail?id=7&id=8)\n[Second record](/article/8)', False),
    ('[First record](/detail?id=7&track=a)\n[First record](/detail?id=7&track=b)', False),
    ('[First record](/article/7?track=a)\n[First record](/article/7?track=b)', False),
    ('[First record](/article/7)\n[Second record](/article/8)', True),
    ('[First record](/detail?id=7)\n[Second record](/detail?id=8)', True),
])
def test_mixed_identity_modes_and_duplicate_controls(host, text, expected):
    assert passes(rules(host), text) is expected


def test_legacy_keys_remain_required_on_every_route():
    contract = rules(); contract.pop('identity_query_paths')
    assert not passes(contract, '[First record](/detail?id=7)\n[Second record](/article/8)')
    assert passes(contract, '[First record](/detail?id=7)\n[Second record](/detail?id=8)')


@pytest.mark.parametrize('value', [[], [''], [' '], ['['], None, '/detail', [3]])
def test_invalid_query_path_configuration(value):
    contract = rules(); contract['identity_query_paths'] = value
    with pytest.raises(ValueError):
        validate_acceptance_contract(contract)


def test_query_paths_need_keys_and_cannot_be_ignored_by_identity_pattern():
    contract = rules(); contract.pop('identity_query_keys')
    with pytest.raises(ValueError): validate_acceptance_contract(contract)
    contract = rules(); contract['entry_identity_pattern'] = r'/(.+)'
    with pytest.raises(ValueError): validate_acceptance_contract(contract)


def test_query_paths_use_canonical_decoded_paths_and_preserve_root_routes():
    contract = rules()
    assert not passes(contract, '[First record](/%64etail)\n[Second record](/article/8)')
    assert passes(contract, '[First record](/%64etail?id=7)\n[Second record](/article/8)')
    contract['entry_path'] = r'(?:|/article/[0-9]+)'
    contract['identity_query_paths'] = ['^$']
    assert passes(contract, '[First record](/?id=7)\n[Second record](/article/8)')
    assert not passes(contract, '[First record](/)\n[Second record](/article/8)')
