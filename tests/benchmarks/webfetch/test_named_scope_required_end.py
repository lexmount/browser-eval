"""Explicit strict text boundaries coexist with legacy body-only responses."""
import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.stable_tasks import validate_acceptance_contract


def contract(host, required=True):
    scope = {'html_selector': 'main.results', 'start_pattern': '^Records$',
             'end_pattern': '^Related topics$'}
    if required is not None:
        scope['required_end'] = required
    return {'version': 1, 'schema': 'named_link', 'minimum_records': 2,
            'hosts': [host], 'entry_path': r'/item/[^/]+', 'entry_scope': scope}


def passes(rules, text):
    case = {'url': 'https://' + rules['hosts'][0] + '/', 'acceptance_contract': rules}
    return task_passed(True, score_attempt(case, text.encode(), ok=True))


@pytest.mark.parametrize('host', ['books.example', 'movies.example'])
def test_strict_end_requires_boundary_and_excludes_outside_records(host):
    rules = contract(host)
    one = f'[First record](https://{host}/item/one)'
    two = f'[Second record](https://{host}/item/two)'
    assert not passes(rules, '# Records\n' + one + '\n# Footer\n' + two)
    assert not passes(rules, '# Records\n' + one + '\n# Related topics\n' + two)
    assert passes(rules, '# Records\n' + one + '\n' + two + '\n# Related topics')


@pytest.mark.parametrize('required', [None, False])
def test_legacy_missing_end_still_accepts_body_only_output(required):
    assert passes(contract('books.example', required),
                  '# Records\n[First record](/item/one)\n[Second record](/item/two)')


@pytest.mark.parametrize('value', ['true', 1, 0, None, {}, []])
def test_required_end_must_be_boolean(value):
    rules = contract('books.example'); rules['entry_scope']['required_end'] = value
    with pytest.raises(ValueError, match='section scope'):
        validate_acceptance_contract(rules)


def test_required_end_requires_declared_pattern():
    rules = contract('books.example'); rules['entry_scope'].pop('end_pattern')
    with pytest.raises(ValueError, match='section scope'):
        validate_acceptance_contract(rules)


def test_html_selector_bounds_records_without_text_end():
    rules = contract('books.example')
    first = '<a href="/item/one">First record</a>'
    second = '<a href="/item/two">Second record</a>'
    assert passes(rules, '<html><main class="results">' + first + second + '</main></html>')
    assert not passes(rules, '<html><main class="results">' + first + '</main><footer>' + second + '</footer></html>')
