"""Declared requirements compose independently of a task's reporting family."""
import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed


CONTRACT = {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
            'hosts': ['example.test'], 'entry_path': r'/item/\d+',
            'stable_text_anchors': []}


@pytest.mark.parametrize('kind', ['body', 'structure', 'resource'])
@pytest.mark.parametrize('missing', [None, 'fact', 'resource', 'record'])
def test_every_declared_requirement_contributes_to_acceptance(kind, missing):
    case = {'url': 'https://example.test/', 'task_kind': kind,
            'acceptance_contract': CONTRACT,
            'required_facts': [{'id': 'description', 'alternatives': ['Battery lasts twelve hours']}],
            'expected_resources': [{'id': 'manual', 'urls': ['/manual.pdf']}]}
    parts = {'fact': 'Battery lasts twelve hours', 'record': '[Portable speaker](/item/1)',
             'resource': '[Manual](/manual.pdf)'}
    answer = '\n'.join(text for key, text in parts.items() if key != missing)
    metrics = score_attempt(case, answer.encode(), ok=True)
    assert task_passed(True, metrics) is (missing is None)


@pytest.mark.parametrize('answer,passed', [
    ('Home\n[Item](/item/1)\nBattery lasts twelve hours', True),
    ('Home\n[Item](/item/1)', False)])
def test_required_facts_remain_required_next_to_a_stable_contract(answer, passed):
    case = {'url': 'https://example.test/', 'acceptance_contract': CONTRACT,
            'required_facts': [{'id': 'detail', 'alternatives': ['Battery lasts twelve hours']}]}
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is passed


@pytest.mark.parametrize('kind', ['body', 'structure', 'resource', 'file'])
@pytest.mark.parametrize('missing', [None, 'fact', 'resource', 'record', 'file'])
def test_file_and_text_requirements_use_the_same_combination(kind, missing):
    from lexbench_webfetch.evaluation import evaluate_response
    case = {'url': 'https://example.test/', 'task_kind': kind,
            'acceptance_contract': CONTRACT,
            'required_facts': [{'id': 'description', 'alternatives': ['Battery lasts twelve hours']}],
            'expected_resources': [{'id': 'manual', 'urls': ['/manual.pdf']}]}
    parts = {'fact': 'Battery lasts twelve hours', 'record': '[Portable speaker](/item/1)',
             'resource': '[Manual](/manual.pdf)'}
    answer = '\n'.join(text for key, text in parts.items() if key != missing).encode()
    files = [] if missing == 'file' else [b'\x89PNG\r\n\x1a\n' + b'x' * 32]
    result = evaluate_response(case, answer, files=files)
    assert result['passed'] is (missing is None or (missing == 'file' and kind != 'file'))


def test_absent_requirements_are_skipped_but_missing_required_evidence_is_not():
    from lexbench_webfetch.evaluation import evaluate_response
    task = {'url': 'https://example.test/', 'task_kind': 'resource'}
    unavailable = evaluate_response(task, b'No reference or resource returned')
    assert not unavailable['passed']
    assert not unavailable['measured']
    assert unavailable['metrics']['acceptance_checks']['resources'] is None
    task = {'url': 'https://example.test/', 'required_facts': [
        {'id': 'body', 'alternatives': ['Article body']} ]}
    accepted = evaluate_response(task, b'Article body')
    assert accepted['passed']
    assert 'facts' in accepted['metrics']['acceptance_checks']
    assert 'resources' not in accepted['metrics']['acceptance_checks']
    assert 'file' not in accepted['metrics']['acceptance_checks']


def test_perfect_tree_does_not_override_a_missing_content_requirement():
    import json
    case = {'url': 'https://example.test/', 'ordered_tree_reference': {'tag': 'table'},
            'ordered_tree_min_similarity': 1.0,
            'required_facts': [{'id': 'value', 'alternatives': ['Quarterly revenue']} ]}
    metrics = score_attempt(case, json.dumps(case['ordered_tree_reference']).encode(), ok=True)
    assert metrics['ordered_tree_similarity'] == 1.0
    assert not task_passed(True, metrics)


@pytest.mark.parametrize('field,value,short_answer', [
    ('expected_any', ['Required introduction'], '[Item](/item/1)'),
    ('min_content_chars', 80, '[Item](/item/1)')])
def test_declared_literal_and_length_requirements_survive_a_record_contract(field, value, short_answer):
    case = {'url': 'https://example.test/', 'acceptance_contract': CONTRACT, field: value}
    assert not task_passed(True, score_attempt(case, short_answer.encode(), ok=True))
    full = 'Required introduction with a full substantive explanation of the portable device.\n[Item](/item/1)'
    assert task_passed(True, score_attempt(case, full.encode(), ok=True))


@pytest.mark.parametrize('track', ['web', 'dynamic', 'challenge', 'risk_control', 'document', 'image', 'video'])
@pytest.mark.parametrize('language,passed', [('en', True), ('fr', False), (None, False)])
def test_declared_document_language_check_does_not_depend_on_track(track, language, passed):
    case = {'url': 'https://example.test/', 'track': track,
            'expected_final_url': 'https://example.test/',
            'expected_document_language': 'en', 'page_identity_required': True}
    metrics = score_attempt(case, b'Article body', ok=True, final_url=case['url'],
                            metadata={'document_language': language})
    assert task_passed(True, metrics) is passed


@pytest.mark.parametrize('kind', ['body', 'structure', 'resource', 'file'])
@pytest.mark.parametrize('complete', [True, False])
def test_declared_body_scope_keeps_truncation_protection_across_output_types(kind, complete):
    from lexbench_webfetch.evaluation import evaluate_response
    task = {'url': 'https://example.test/', 'task_kind': kind,
            'acceptance_contract': CONTRACT,
            'expected_resources': [{'id': 'image', 'urls': ['/image.png']}],
            'body_requirements': {'version': 1, 'unit': 'region_token_recall', 'minimum_coverage': 0.8,
                'regions': [{'id': 'body', 'text': 'alpha beta gamma delta epsilon zeta eta theta iota kappa'}]}}
    answer = ('alpha beta gamma delta epsilon zeta eta theta iota\n'
              '[Item](/item/1)\n![Image](/image.png)\n'
              '(Content truncated. Fetch a more specific URL or section for the full text.)')
    if complete:
        answer = answer.replace('theta iota\n', 'theta iota kappa\n')
    result = evaluate_response(task, answer.encode(), files=[b'\x89PNG\r\n\x1a\n'+b'x'*32])
    assert result['passed'] is complete


@pytest.mark.parametrize('track', ['web', 'dynamic', 'document'])
@pytest.mark.parametrize('language,passed', [('', True), ('en', False), (None, False)])
def test_declared_absent_language_tag_is_checked_without_locale_or_redirect_fields(track, language, passed):
    case = {'url': 'https://example.test/', 'track': track,
            'expected_document_language_present': False, 'page_identity_required': True}
    metrics = score_attempt(case, b'Article body', ok=True, final_url=case['url'],
                            metadata={'document_language': language})
    assert task_passed(True, metrics) is passed


def test_dict_and_dataclass_apply_the_same_default_text_requirement():
    from lexbench_webfetch.models import EvalCase
    links = {'links': [{'text': 'Portable speaker', 'href': '/item/1'}]}
    common = {'id': 'sample', 'name': 'Sample', 'url': 'https://example.test/',
              'category': 'test', 'expected_any': [], 'task_kind': 'structure',
              'acceptance_contract': CONTRACT}
    for applicable, passed in [(True, False), (False, True)]:
        task = {**common, 'content_score_applicable': applicable}
        for case in (task, EvalCase(**task)):
            assert task_passed(True, score_attempt(case, b'', ok=True, metadata=links)) is passed


def test_low_level_content_checks_keep_declared_literals_and_length():
    from lexbench_webfetch.metrics import evaluate_content
    assert evaluate_content('[Portable speaker](/item/1)', [], acceptance_contract=CONTRACT,
                            base_url='https://example.test/')['content_pass']
    for fields in ({'expected': ['Required introduction']}, {'min_content_chars': 1000}):
        arguments = {'expected': [], **fields}
        metrics = evaluate_content('[Portable speaker](/item/1)', acceptance_contract=CONTRACT,
                                   base_url='https://example.test/', **arguments)
        assert not metrics['content_pass']


def test_omitted_literal_check_cannot_invalidate_proven_complete_body():
    from lexbench_webfetch.evaluation import evaluate_response
    case = {'url': 'https://example.test/', 'acceptance_contract': CONTRACT,
            'content_score_applicable': False, 'expected_any': ['Optional page shell'],
            'body_requirements': {'version': 1, 'unit': 'region_token_recall',
                'minimum_coverage': 0.8, 'regions': [{'id': 'body', 'text': 'alpha beta gamma delta epsilon'}]}}
    result = evaluate_response(case, b'alpha beta gamma delta epsilon\n[Speaker](/item/1)\n(Content truncated. Fetch a more specific URL or section for the full text.)')
    assert result['passed']


def test_frozen_metric_projection_keeps_its_recorded_acceptance_semantics():
    frozen = {'scoring_source_sha256': 'a' * 64, 'stable_task_pass': True,
              'content_pass': False, 'content_score_applicable': False,
              'stable_anchor_results': [{'pass': True}]}
    assert task_passed(True, frozen)
    # A failed declared pattern remained a failure in the historical format.
    assert not task_passed(True, {**frozen, 'pattern_results': [{'pass': False}]})
    current = score_attempt({'url': 'https://example.test/',
        'acceptance_contract': CONTRACT, 'required_facts': [
            {'id': 'required', 'alternatives': ['Battery lasts twelve hours']}]},
        b'[Speaker](/item/1)', ok=True)
    assert not task_passed(True, current)
    assert 'acceptance_checks' not in frozen


@pytest.mark.parametrize('answer,passed', [
    ('[Speaker](/item/1)', True), ('Account Privacy Terms', False)])
def test_page_shell_literals_can_be_diagnostic_when_records_own_acceptance(answer, passed):
    case = {'url': 'https://example.test/', 'acceptance_contract': {
        **CONTRACT, 'stable_text_role': 'diagnostic',
        'stable_text_anchors': [{'id': 'shell', 'alternatives': ['Account Privacy Terms']}]}}
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is passed


def test_imported_tasks_apply_the_same_record_owned_acceptance_role():
    from lexbench_webfetch.risk_control_dataset import assign_content_acceptance_roles
    case = {'acceptance_contract': {**CONTRACT, 'stable_text_role': 'diagnostic'}}
    assign_content_acceptance_roles([case])
    assert case['acceptance_contract']['stable_text_role'] == 'diagnostic'


@pytest.mark.parametrize('kind', ['body', 'structure'])
@pytest.mark.parametrize('answer,passed', [('Battery lasts twelve hours', True), ('Home Terms', False)])
def test_facts_do_not_require_an_undeclared_record_check(kind, answer, passed):
    case = {'url': 'https://example.test/', 'task_kind': kind,
            'required_facts': [{'id': 'duration', 'alternatives': ['Battery lasts twelve hours']}]}
    result = score_attempt(case, answer.encode(), ok=True)
    assert task_passed(True, result) is passed
    assert 'records' not in result['acceptance_checks']


@pytest.mark.parametrize('track', ['web', 'dynamic', 'challenge', 'risk_control', 'articles', 'document'])
@pytest.mark.parametrize('kind', ['body', 'structure', 'resource', 'file'])
@pytest.mark.parametrize('missing', [None, 'title', 'comments'])
def test_optional_content_components_share_the_declared_acceptance_inventory(track, kind, missing):
    from .article_contract_fixtures import comment_labels
    from lexbench_webfetch.evaluation import evaluate_response
    case = {
        'url': 'https://example.test/', 'track': track, 'task_kind': kind,
        'min_content_chars': 1, 'acceptance_contract': CONTRACT,
        'expected_resources': [{'id': 'manual', 'urls': ['/manual.pdf']}],
        'source_benchmark': {
            'title_contract': {'version': 1, 'alternatives': ['Why We Read'], 'html_selector': 'h1'},
            'comment_contract': {**comment_labels(), 'version': 2, 'record_selector': '.comment',
                'author_selector': '.author', 'body_selector': '.text',
                'section_markers': ['Comments'], 'minimum_comments': 1},
        },
    }
    title = '# Another article' if missing == 'title' else '# Why We Read'
    answer = title + '\n\nArticle body.\n\n[Portable speaker](/item/1)\n\n[Manual](/manual.pdf)'
    if missing != 'comments':
        answer += '\n\nComments\n\n## Reader\nA thoughtful book.'
    result = evaluate_response(case, answer.encode(), files=[b'\x89PNG\r\n\x1a\n' + b'x' * 32])
    checks = result['metrics']['acceptance_checks']
    assert checks['article_title'] is (missing != 'title')
    assert checks['article_comments'] is (missing != 'comments')
    assert result['passed'] is (missing is None)


@pytest.mark.parametrize('kind', ['body', 'structure', 'resource', 'file'])
@pytest.mark.parametrize('complete', [True, False])
def test_disabled_text_diagnostics_do_not_add_acceptance_requirements(kind, complete):
    from lexbench_webfetch.evaluation import evaluate_response
    case = {'url': 'https://example.test/', 'task_kind': kind,
            'content_score_applicable': False, 'min_content_chars': 1000,
            'expected_any': ['Optional introductory text'],
            'required_facts': [{'id': 'battery', 'alternatives': ['Twelve hours']}],
            'expected_resources': [{'id': 'manual', 'urls': ['/manual.pdf']}]}
    answer = ('Twelve hours' if complete else 'Unknown duration') + '\n[Manual](/manual.pdf)'
    result = evaluate_response(case, answer.encode(), files=[b'\x89PNG\r\n\x1a\n' + b'x' * 32])
    assert result['passed'] is complete
    assert 'length' not in result['metrics']['acceptance_checks']
    assert 'literals' not in result['metrics']['acceptance_checks']
