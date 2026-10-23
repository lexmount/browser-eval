"""Report coverage describes declarations, not inferred retrieval capabilities."""
from copy import deepcopy
from harness.web_fetch.report_components import acceptance_scope


def test_scope_separates_literal_facts_body_and_records_without_changing_verdicts():
    cases = {
        'navigation': {'id': 'navigation', 'track': 'popular', 'expected_any': ['Home'], 'reference_answer': 'Instruction to read the page'},
        'article': {'id': 'article', 'track': 'dynamic', 'page_identity_required': True,
                    'ground_truth_main_content': 'Full independent article', 'required_facts': [{'id': 'fact'}], 'body_requirements': {'regions': ['body']},
                    'acceptance_contract': {'minimum_records': 3}},
        'resource': {'id': 'resource', 'track': 'web', 'task_kind': 'resource',
                     'expected_resources': [{'id': 'video'}]},
    }
    rows = [
        {'provider': 'service', 'case_id': 'navigation', 'ok': True, 'metrics': {'content_pass': True}},
        {'provider': 'service', 'case_id': 'article', 'ok': True,
         'metrics': {'content_pass': True, 'forbidden_requirement_hits': [], 'page_identity_required': True, 'page_identity_status': 'verified', 'blocked_page': True}},
        {'provider': 'service', 'case_id': 'resource', 'ok': True,
         'metrics': {'resource_pass': True, 'resource_precision': .5, 'unexpected_resource_urls': ['other']}},
    ]
    before = deepcopy((cases, rows))
    scope = acceptance_scope(cases, rows)
    assert scope['families']['popular']['literal'] == 1
    assert scope['families']['popular']['body_regions'] == scope['families']['popular']['full_reference'] == 0
    assert scope['families']['dynamic']['full_reference'] == 1
    assert scope['families']['dynamic']['facts'] == scope['families']['dynamic']['body_regions'] == 1
    assert scope['families']['dynamic']['records'] == scope['families']['dynamic']['identity'] == 1
    method = scope['methods']['service']
    assert method['passes'] == 3 and method['unverified_identity_passes'] == 2
    assert method['blocked_diagnostics'] == method['resource_results'] == method['unexpected_resources'] == 1
    assert method['resource_precision_sum'] == .5
    assert (cases, rows) == before


def test_affiliation_is_exact_repository_scope_not_a_similar_name():
    cases = {name: {'id': name, 'url': url} for name, url in {
        'owned': 'https://github.com/lexmount/moli',
        'readme': 'https://github.com/lexmount/moli/blob/main/README.md',
        'other': 'https://github.com/lexmount/molina',
        'external': 'https://github.com/another/moli',
    }.items()}
    assert acceptance_scope(cases, [])['affiliated_cases'] == ['owned', 'readme']
