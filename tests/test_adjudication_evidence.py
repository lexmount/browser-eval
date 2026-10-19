import hashlib

import pytest

from harness.web_fetch.adjudication_evidence import (
    adjudication_reason, case_hash, current_rule_outcomes, require_adjudication_evidence,
)
from lexbench_webfetch.scoring import score_attempt


@pytest.mark.parametrize('defect', [None, 'case', 'answer', 'context', 'opinion',
                                  'original_opinion', 'original_identity'])
def test_compact_retention_keeps_actual_opinion_without_old_matrices(tmp_path, defect):
    import copy
    import json
    from harness.web_fetch.adjudication_evidence import (
        compact_retained_review_dependencies, require_retained_reviews)
    text = 'Required complete article.'
    (tmp_path/'answer.txt').write_text(text)
    old_case = {'id': 'a', 'url': 'https://example.test/',
        'required_facts': [{'id': 'article', 'alternatives': [text]}], 'forbidden_any': ['CAPTCHA']}
    case = {k: v for k, v in old_case.items() if k != 'forbidden_any'}
    case['diagnostic_any'] = ['CAPTCHA']
    record = {'attempt_id': 'p__a', 'case_id': 'a', 'provider': 'p', 'ok': True,
        'artifact_path': 'answer.txt', 'metrics': score_attempt(case, text.encode(), ok=True)}
    opinion = {'attempt_id': 'p__a', 'content_sha256': record['metrics']['content_sha256'],
        'agent_verdict': 'pass', 'agent_evidence': 'Complete article observed.',
        'final_verdict': 'pass', 'original_explanation': 'Full original opinion retained.'}
    def bind(name, value):
        path = tmp_path/name
        path.write_text(json.dumps(value))
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    results = bind('old-results.jsonl', record)
    prior = bind('old-review.json', {'results_sha256': results['sha256'], 'attempts': [opinion]})
    old_dataset = bind('old-dataset.json', {'cases': [old_case]})
    review = {'attempts': [{**copy.deepcopy(opinion), 'review_origin': 'retained'}],
        'retained_reviewed_attempts': 1, 'fresh_reviewed_attempts': 0,
        'retained_reviews': bind('retention.json', {'kind': 'unchanged_review_dependency_retention',
            'provider_calls_added': 0, 'attempt_ids': ['p__a'], 'prior_results': results,
            'prior_review': prior, 'prior_dataset': old_dataset})}
    compact = compact_retained_review_dependencies(tmp_path, {'cases': [case]}, [record], review)
    assert compact['attempts'][0]['original_opinion'] == opinion
    for name in ['old-results.jsonl', 'old-review.json', 'old-dataset.json', 'retention.json']:
        (tmp_path/name).unlink()
    if defect == 'case':
        case['required_facts'] = ['Different requirement']
    elif defect == 'answer':
        (tmp_path/'answer.txt').write_text('Changed')
    elif defect == 'context':
        record['ok'] = False
    elif defect == 'opinion':
        review['attempts'][0]['agent_evidence'] = 'Rewritten opinion'
    elif defect == 'original_opinion':
        compact['attempts'][0]['original_opinion']['original_explanation'] = 'Rewritten original'
    elif defect == 'original_identity':
        compact['attempts'][0]['original_review_input']['request_ok'] = False
    review['retained_reviews'] = bind('compact.json', compact)
    if defect:
        with pytest.raises(ValueError):
            require_retained_reviews(tmp_path, {'cases': [case]}, [record], review)
    else:
        assert require_retained_reviews(tmp_path, {'cases': [case]}, [record], review) == {'p__a'}


def fixture(tmp_path):
    text = 'Required complete article.'
    (tmp_path / 'answer.txt').write_text(text)
    case = {'id': 'a', 'url': 'https://example.test/', 'required_facts': [
        {'id': 'article', 'alternatives': [text]}], 'diagnostic_any': ['CAPTCHA']}
    metrics = score_attempt(case, text.encode(), ok=True)
    record = {'attempt_id': 'p__a', 'case_id': 'a', 'ok': True,
              'artifact_path': 'answer.txt', 'metrics': metrics}
    assertions = current_rule_outcomes(case, record)
    reason = adjudication_reason(assertions)
    row = {'attempt_id': 'p__a', 'agent_verdict': 'fail', 'final_verdict': 'pass',
           'adjudication': reason, 'adjudication_evidence': {
               'reason': reason, 'case_sha256': case_hash(case),
               'content_sha256': metrics['content_sha256'],
               'rule_ids': list(assertions), 'rule_assertions': assertions,
               'samples': [{'start': 0, 'end': len(text), 'text': text}]}}
    return case, record, row


@pytest.mark.parametrize('defect', [None, 'unknown_rule', 'false_hit', 'false_result',
    'false_reason', 'empty_hit_claim', 'excerpt', 'verdict', 'historical_migration'])
def test_adjudication_rejects_semantic_falsehoods_not_only_missing_text(tmp_path, defect):
    case, record, row = fixture(tmp_path)
    proof = row['adjudication_evidence']
    review = {'attempts': [row]}
    if defect == 'unknown_rule':
        proof['rule_ids'].append('nonexistent-rule')
    elif defect == 'false_hit':
        proof['rule_assertions']['diagnostic_any']['hits'] = ['CAPTCHA']
    elif defect == 'false_result':
        proof['rule_assertions']['required_facts']['passed'] = False
    elif defect == 'false_reason':
        proof['reason'] = row['adjudication'] = 'CAPTCHA 已命中；正文通过。'
    elif defect == 'empty_hit_claim':
        proof['reason'] = row['adjudication'] = '命中词：。因此原意见不成立。'
    elif defect == 'excerpt':
        proof['samples'][0]['text'] = 'Invented article'
    elif defect == 'verdict':
        row['final_verdict'] = 'fail'
        row['agent_verdict'] = 'pass'
    elif defect == 'historical_migration':
        review['rule_binding_revision'] = {'new_independent_review': False}
    if defect:
        with pytest.raises(ValueError):
            require_adjudication_evidence(tmp_path, {'cases': [case]}, [record], review)
    else:
        assert require_adjudication_evidence(tmp_path, {'cases': [case]}, [record], review) == 1
        assert '诊断词命中：无' in row['adjudication']


@pytest.mark.parametrize('defect', [None, 'answer', 'excerpt', 'opinion', 'case', 'binding'])
def test_supplemental_review_binds_new_opinion_task_and_complete_answer(tmp_path, defect):
    import copy
    import json
    from harness.web_fetch.adjudication_evidence import _require_supplemental_reviews
    text = 'Complete document and sqlite3 test.db'
    (tmp_path/'answer.txt').write_text(text)
    case = {'id': 'a', 'required_facts': ['sqlite3 test.db']}
    record = {'attempt_id': 'p__a', 'case_id': 'a', 'artifact_path': 'answer.txt',
              'metrics': {'content_sha256': hashlib.sha256(text.encode()).hexdigest()}}
    row = {'attempt_id': 'p__a', 'agent_verdict': 'pass', 'agent_evidence': 'Required command is present.',
           'content_sha256': record['metrics']['content_sha256'],
           'review_evidence': {'case_sha256': case_hash(case),
               'samples': [{'start': 0, 'end': len(text), 'text': text}]}}
    supplement = {'kind': 'supplemental_saved_output_review', 'results_sha256': 'a'*64,
        'provider_calls_added': 0, 'reviewed_attempts': 1, 'reviewer': {'agent': 'independent reviewer'},
        'review_protocol': {'blind_initial_verdict': False}, 'attempts': [copy.deepcopy(row)]}
    if defect == 'excerpt':
        row['review_evidence']['samples'][0]['text'] = 'Invented answer'
        supplement['attempts'] = [copy.deepcopy(row)]
    path = tmp_path/'supplement.json'
    path.write_text(json.dumps(supplement))
    binding = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    review = {'attempts': [row], 'results_sha256': 'a'*64, 'supplemental_reviews': [binding]}
    if defect == 'answer': (tmp_path/'answer.txt').write_text('Changed answer')
    elif defect == 'opinion': row['agent_verdict'] = 'fail'
    elif defect == 'case': case['required_facts'] = ['different command']
    elif defect == 'binding': binding['sha256'] = 'b'*64
    if defect:
        with pytest.raises(ValueError):
            _require_supplemental_reviews(tmp_path, {'cases': [case]}, [record], review)
    else:
        assert _require_supplemental_reviews(tmp_path, {'cases': [case]}, [record], review) == {'p__a'}


@pytest.mark.parametrize('defect', [None, 'positive_rule', 'answer', 'opinion', 'verdict',
    'policy_hit', 'dependent_reason', 'prior_binding', 'count', 'missing_receipt'])
def test_retention_requires_unchanged_decision_dependencies(tmp_path, defect):
    import copy
    import json
    from harness.web_fetch.adjudication_evidence import require_retained_reviews
    from harness.web_fetch.result_review import review_input_identity
    text = 'Required complete article.'
    if defect == 'policy_hit': text += ' CAPTCHA'
    old_case = {'id': 'a', 'url': 'https://example.test/',
                'required_facts': [{'id': 'article', 'alternatives': ['Required complete article.']}],
                'forbidden_any': ['CAPTCHA']}
    case = {k: v for k, v in old_case.items() if k != 'forbidden_any'}
    case['diagnostic_any'] = ['CAPTCHA']
    (tmp_path/'answer.txt').write_text(text)
    metrics = score_attempt(case, text.encode(), ok=True)
    record = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
              'artifact_path': 'answer.txt', 'metrics': metrics}
    original = {'attempt_id': 'p__a', 'content_sha256': metrics['content_sha256'],
                'agent_verdict': 'pass', 'agent_evidence': 'Required article is complete.',
                'final_verdict': 'pass'}
    if defect == 'dependent_reason':
        original['adjudication_evidence'] = {'rule_ids': ['forbidden_any']}
    def bind(name, value, *, jsonl=False):
        path = tmp_path/name
        path.write_text(json.dumps(value) + ('\n' if jsonl else ''))
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    old_record = copy.deepcopy(record)
    assert review_input_identity(record) == review_input_identity(old_record)
    result_binding = bind('old-results.jsonl', old_record, jsonl=True)
    prior = {'results_sha256': result_binding['sha256'], 'attempts': [original]}
    if defect == 'prior_binding': prior['results_sha256'] = 'x'*64
    receipt = {'kind': 'unchanged_review_dependency_retention', 'provider_calls_added': 0,
        'attempt_ids': ['p__a'], 'prior_results': result_binding,
        'prior_dataset': bind('old-dataset.json', {'cases': [old_case]}),
        'prior_review': bind('old-review.json', prior)}
    row = {**copy.deepcopy(original), 'review_origin': 'retained'}
    review = {'attempts': [row], 'retained_reviewed_attempts': 1,
              'fresh_reviewed_attempts': 0, 'retained_reviews': bind('retention.json', receipt)}
    if defect == 'positive_rule': case['required_facts'] = ['A different article']
    elif defect == 'answer': (tmp_path/'answer.txt').write_text('Changed answer')
    elif defect == 'opinion': row['agent_evidence'] = 'Rewritten opinion'
    elif defect == 'verdict': row['final_verdict'] = 'fail'
    elif defect == 'count': review['fresh_reviewed_attempts'] = 1
    elif defect == 'missing_receipt': review.pop('retained_reviews')
    if defect:
        with pytest.raises(ValueError):
            require_retained_reviews(tmp_path, {'cases': [case]}, [record], review)
    else:
        assert require_retained_reviews(tmp_path, {'cases': [case]}, [record], review) == {'p__a'}


@pytest.mark.parametrize('defect', [None, 'opinion', 'reason', 'anomaly', 'unblinded', 'missing'])
def test_fresh_review_keeps_the_immutable_blind_initial_opinion(tmp_path, defect):
    import copy
    import json
    from harness.web_fetch.adjudication_evidence import _require_supplemental_reviews
    case, record, row = fixture(tmp_path)
    text = (tmp_path/'answer.txt').read_text()
    row.update(content_sha256=record['metrics']['content_sha256'],
               agent_evidence='The required article is absent.', reviewer_anomalies=[],
               review_evidence={'case_sha256': case_hash(case),
                   'samples': [{'start': 0, 'end': len(text), 'text': text}]})
    initial = {'machine_judgments_withheld': defect != 'unblinded', 'attempts': [{
        'attempt_id': 'p__a', 'verdict': 'fail', 'reason': row['agent_evidence'], 'anomalies': []}]}
    if defect == 'missing': initial['attempts'] = []
    if defect == 'opinion': row['agent_verdict'] = 'pass'
    elif defect == 'reason': row['agent_evidence'] = 'Rewritten initial opinion'
    elif defect == 'anomaly': row['reviewer_anomalies'] = ['Invented anomaly']
    def bind(name, document):
        path = tmp_path/name
        path.write_text(json.dumps(document))
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    supplement = {'kind': 'supplemental_saved_output_review', 'provider_calls_added': 0,
        'results_sha256': 'a'*64, 'reviewed_attempts': 1, 'reviewer': {'model': 'reviewer'},
        'review_protocol': {'blind_initial_verdict': True},
        'blind_initial_outputs': [bind('initial.json', initial)], 'attempts': [copy.deepcopy(row)]}
    review = {'results_sha256': 'a'*64, 'attempts': [row],
              'supplemental_reviews': [bind('supplement.json', supplement)]}
    if defect:
        with pytest.raises(ValueError):
            _require_supplemental_reviews(tmp_path, {'cases': [case]}, [record], review)
    else:
        assert _require_supplemental_reviews(tmp_path, {'cases': [case]}, [record], review) == {'p__a'}
