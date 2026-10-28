"""API tuples retain field ownership when a converter leaves literal link syntax."""
import pytest

from lexbench_webfetch.dynamic_entry_matching import matching_entry_facts
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.metrics import fact_in_text, normalize_for_match
from lexbench_webfetch.reference_quality import visible_markdown_links


FIELDS = [('method', 'POST'), ('path', '/pets'), ('summary', 'Create a pet')]
FACTS = [{'id': 'entry-1-' + field, 'alternatives': [value]} for field, value in FIELDS]
PREFIX = '# API```\n1.0\n``````\nSpecification\n```\n\n'
ROUTE_CASES = [
    ('POST [/pets](#/create) Create a pet', True),
    ('POST[/pets](#/create)Create a pet', True),
    ('POST [/pets](#/create(v1) "Operation") Create a pet', True),
    ('POST /pets Create a pet', True),
    ('GET [/pets](#/create) Create a pet', False),
    ('POST [/pets/extra](#/create) Create a pet', False),
    ('POST [Documentation](/pets) Create a pet', False),
    ('POST [/pets](#/create) Unrelated field Create a pet', False),
    ('POST [/pets](#/Create a pet) Missing summary', False),
]


@pytest.mark.parametrize('record,expected', ROUTE_CASES)
def test_literal_route_links_keep_complete_tuple_boundaries(record, expected):
    text = PREFIX + record
    folded = normalize_for_match(text)
    hits = matching_entry_facts(folded, FACTS, normalize_for_match, fact_in_text)
    assert all(hits.values()) is expected
    task = {'url': 'https://example.test/api', 'required_facts': FACTS}
    assert evaluate_response(task, text.encode())['passed'] is expected


def test_tuple_projection_does_not_remove_literal_urls_from_body_facts():
    text = PREFIX + 'POST [/pets](#/create) Create a pet'
    assert visible_markdown_links(text) == text
    assert '#/create' in normalize_for_match(text)
    task = {'url': 'https://example.test/api',
            'required_facts': [{'id': 'documentation-link', 'alternatives': ['#/create']}]}
    assert evaluate_response(task, text.encode())['passed']


def scoring_controls():
    for index, (record, expected) in enumerate(ROUTE_CASES):
        yield {'id': f'literal-api-route-{index}', 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Literal API link wrappers interrupt complete method/path/summary tuples; only declared path labels own the route field.',
               'case': {'url': 'https://example.test/api', 'required_facts': FACTS},
               'artifact': PREFIX + record, 'ok': True, 'expected_task_pass': expected}
    for literal in (False, True):
        text = 'POST [/pets](#/create) Create a pet'
        yield {'id': f'literal-api-body-url-{literal}', 'category': 'answer-surface',
               'failure_mode': 'false-negative' if literal else 'false-positive',
               'root_cause': 'Local API tuple projection must not alter ordinary body facts or hide URLs displayed in code examples.',
               'case': {'url': 'https://example.test/api',
                        'required_facts': [{'id': 'documentation-link', 'alternatives': ['#/create']}]},
               'artifact': PREFIX + text if literal else text,
               'ok': True, 'expected_task_pass': literal}
