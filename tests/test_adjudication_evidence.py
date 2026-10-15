import hashlib

import pytest

from harness.web_fetch.adjudication_evidence import case_hash, require_adjudication_evidence, TEMPLATE


def test_overridden_review_requires_rules_and_verifiable_answer_excerpt(tmp_path):
    text = 'Home\n0:00 / Live · Watch full video'
    (tmp_path / 'answer.txt').write_text(text)
    case = {'id': 'a', 'task_prompt': 'Player controls do not substitute for ordinary text'}
    record = {'attempt_id': 'p__a', 'case_id': 'a', 'artifact_path': 'answer.txt',
              'metrics': {'content_sha256': hashlib.sha256(text.encode()).hexdigest()}}
    row = {'attempt_id': 'p__a', 'agent_verdict': 'pass', 'final_verdict': 'fail', 'adjudication': TEMPLATE}
    def check():
        return require_adjudication_evidence(tmp_path, {'cases': [case]}, [record], {'attempts': [row]})
    with pytest.raises(ValueError, match='specific rule/evidence'):
        check()
    row['adjudication'] = 'Live occurs only in the excluded player control, so the required ordinary-text group is missing.'
    row['adjudication_evidence'] = {'reason': row['adjudication'], 'case_sha256': case_hash(case),
        'content_sha256': record['metrics']['content_sha256'], 'rule_ids': ['ordinary-text:Live'],
        'samples': [{'start': 5, 'end': len(text), 'text': text[5:]}]}
    assert check() == 1
    row['adjudication_evidence']['samples'][0]['text'] = 'invented evidence'
    with pytest.raises(ValueError, match='excerpt differs'):
        check()


@pytest.mark.parametrize('defect', [None, 'opinion', 'verdict', 'excerpt', 'target_rule', 'missing_receipt'])
def test_diagnostic_field_migration_preserves_historical_review(tmp_path, defect):
    import copy
    import json
    from harness.web_fetch.adjudication_evidence import _require_review_binding_revision
    raw = b'Required body. CAPTCHA'
    content_hash = hashlib.sha256(raw).hexdigest()
    old = {'id': 'a', 'required_facts': [{'id': 'body', 'alternatives': ['Required body']}],
           'forbidden_any': ['CAPTCHA']}
    current = {k: v for k, v in old.items() if k != 'forbidden_any'}
    current['diagnostic_any'] = ['CAPTCHA']
    row = {'attempt_id': 'p__a', 'case_id': 'a', 'content_sha256': content_hash,
           'agent_verdict': 'fail', 'current_verdict': 'pass', 'final_verdict': 'pass',
           'adjudication': 'Required body is present; the word is diagnostic.',
           'adjudication_evidence': {'case_sha256': case_hash(old), 'rule_ids': ['required_facts', 'forbidden_any'],
               'content_sha256': content_hash, 'samples': [{'text': 'Required body'}]}}
    def save(name, value):
        path = tmp_path/name
        path.write_text(json.dumps(value))
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    old_review = save('old-review.json', {'results_sha256': 'a'*64, 'attempts': [row]})
    old_dataset = save('old-dataset.json', {'cases': [old]})
    receipt = save('score-revision.json', {'rule_revision': True, 'provider_calls_added': 0,
        'authority': {'preserved_render_input_results_sha256': 'a'*64}, 'input_dataset': old_dataset})
    migrated = copy.deepcopy(row)
    proof = migrated['adjudication_evidence']
    proof['prior_case_sha256'] = proof['case_sha256']
    proof['case_sha256'] = case_hash(current)
    proof['rule_ids'] = ['required_facts', 'diagnostic_any']
    review = {'attempts': [migrated], 'rule_binding_revision': {
        'kind': 'unchanged_target_and_verdict_evidence_carryforward', 'new_independent_review': False,
        'prior_review': old_review, 'score_revision': receipt, 'attempt_ids': ['p__a']}}
    if defect == 'opinion': migrated['agent_verdict'] = 'pass'
    elif defect == 'verdict': migrated['final_verdict'] = 'fail'
    elif defect == 'excerpt': proof['samples'][0]['text'] = 'Invented body'
    elif defect == 'target_rule': current['required_facts'] = []
    elif defect == 'missing_receipt': review.pop('rule_binding_revision')
    records = [{'attempt_id': 'p__a', 'metrics': {'content_sha256': content_hash}}]
    if defect:
        with pytest.raises(ValueError):
            _require_review_binding_revision(tmp_path, {'cases': [current]}, records, review)
    else:
        _require_review_binding_revision(tmp_path, {'cases': [current]}, records, review)
