"""Require inspectable task and answer evidence when a verdict overrides review."""
import hashlib
import json

from .safety import contained_path


TEMPLATE = '核对已有原始返回、冻结要求和相应修正证据；当前结论见评分明细。'


def case_hash(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def requires_adjudication(row):
    final = row.get('final_verdict')
    return final is not None and any(
        row.get(key) not in {None, final} for key in ('agent_verdict', 'effective_agent_verdict'))


def require_adjudication_evidence(source, dataset, records, review):
    _require_review_binding_revision(source, dataset, records, review)
    cases = {case['id']: case for case in dataset.get('cases', [])}
    indexed = {record['attempt_id']: record for record in records}
    checked = 0
    for row in review.get('attempts', []):
        if not requires_adjudication(row):
            continue
        record = indexed[row['attempt_id']]
        proof = row.get('adjudication_evidence', {})
        reason = proof.get('reason', '').strip()
        if (not reason or reason == TEMPLATE or row.get('adjudication') == TEMPLATE
                or proof.get('case_sha256') != case_hash(cases[record['case_id']])
                or proof.get('content_sha256') != record['metrics']['content_sha256']
                or not proof.get('rule_ids')):
            raise ValueError('Overridden review lacks specific rule/evidence: ' + row['attempt_id'])
        raw = contained_path(source, record['artifact_path'], reject_symlinks=True).read_bytes()
        if hashlib.sha256(raw).hexdigest() != proof['content_sha256']:
            raise ValueError('Adjudication answer changed')
        text = raw.decode('utf-8', errors='replace')
        samples = proof.get('samples', [])
        if not text:
            if proof.get('empty_output') is not True:
                raise ValueError('Empty adjudication answer must be explicit')
        elif not samples:
            raise ValueError('Adjudication lacks an answer excerpt')
        for sample in samples:
            start, end = sample.get('start'), sample.get('end')
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text)
                    or text[start:end] != sample.get('text')):
                raise ValueError('Adjudication excerpt differs from original answer')
        checked += 1
    return checked


def require_current_review_state(records, review):
    from .metrics import task_measured, task_passed
    indexed={record['attempt_id']:record for record in records}
    rows=review.get('attempts',[])
    unreviewed=review.get('unreviewed_attempt_ids',[])
    reviewed_ids={row['attempt_id'] for row in rows}
    if (len(rows)!=len(reviewed_ids) or len(unreviewed)!=len(set(unreviewed))
            or reviewed_ids & set(unreviewed) or reviewed_ids | set(unreviewed)!=set(indexed)):
        raise ValueError('Review state inventory differs from current results')
    if unreviewed and (review.get('reviewed_attempts')!=len(rows)
            or review.get('accepted_unreviewed_passes')!=sum(
                task_passed(indexed[k]['ok'],indexed[k]['metrics']) for k in unreviewed)):
        raise ValueError('Unreviewed result coverage differs from current scores')
    for row in rows:
        record=indexed[row['attempt_id']]
        expected='unavailable' if not task_measured(record['metrics']) else 'pass' if task_passed(record['ok'],record['metrics']) else 'fail'
        if row.get('current_verdict')!=expected or row.get('final_verdict') not in {expected,None}:
            raise ValueError('Review state differs from current scored result')
        if requires_adjudication(row) and row.get('status')!='adjudicated':
            raise ValueError('Review override has no adjudicated state')
    unresolved=sum(row.get('final_verdict') is None for row in rows)
    if review.get('unresolved')!=unresolved:
        raise ValueError('Review unresolved total differs from result states')
    effective=sum(row.get('effective_agent_verdict',row.get('agent_verdict'))!=row.get('final_verdict') for row in rows)
    if review.get('effective_disagreements',effective)!=effective:
        raise ValueError('Review disagreement total differs from result states')


def _require_review_binding_revision(source, dataset, records, review):
    """Carry historical evidence only across a declared hint-field migration."""
    import copy
    from .local_revision import read_bound
    migration = review.get('rule_binding_revision')
    if not migration:
        if any('prior_case_sha256' in row.get('adjudication_evidence', {}) for row in review.get('attempts', [])):
            raise ValueError('Historical review binding migration lacks its receipt')
        return
    if (migration.get('kind') != 'unchanged_target_and_verdict_evidence_carryforward'
            or migration.get('new_independent_review') is not False):
        raise ValueError('Historical evidence migration cannot claim new independent review')
    previous = json.loads(read_bound(source, migration['prior_review']).read_text())
    receipt = json.loads(read_bound(source, migration['score_revision']).read_text())
    if (receipt.get('rule_revision') is not True or receipt.get('provider_calls_added') != 0
            or receipt.get('authority', {}).get('preserved_render_input_results_sha256') != previous.get('results_sha256')):
        raise ValueError('Historical review migration has no matching score revision')
    old_dataset = json.loads(read_bound(source, receipt['input_dataset']).read_text())
    old_cases = {case['id']: case for case in old_dataset['cases']}
    cases = {case['id']: case for case in dataset['cases']}
    indexed = {record['attempt_id']: record for record in records}
    old_rows = {row['attempt_id']: row for row in previous['attempts']}
    rows = review['attempts']
    if {row['attempt_id'] for row in rows} != set(old_rows) or len(rows) != len(old_rows):
        raise ValueError('Historical review migration changed review coverage')
    carried = []
    for row in rows:
        expected = copy.deepcopy(old_rows[row['attempt_id']])
        old = old_cases[row['case_id']]
        normalized = copy.deepcopy(old)
        hints = normalized.pop('forbidden_any', [])
        if hints:
            normalized['diagnostic_any'] = list(dict.fromkeys(normalized.get('diagnostic_any', []) + hints))
        normalized.pop('acceptance_policy_version', None)
        if normalized != cases[row['case_id']]:
            raise ValueError('Historical review migration changed target requirements')
        proof = expected.get('adjudication_evidence')
        if proof:
            if proof['case_sha256'] != case_hash(old):
                raise ValueError('Historical review proof does not bind the original case')
            proof['prior_case_sha256'] = proof['case_sha256']
            proof['case_sha256'] = case_hash(cases[row['case_id']])
            proof['rule_ids'] = ['diagnostic_any' if key == 'forbidden_any' else key for key in proof['rule_ids']]
            carried.append(row['attempt_id'])
        if row != expected or row.get('content_sha256') != indexed[row['attempt_id']]['metrics']['content_sha256']:
            raise ValueError('Historical review migration changed opinions, verdicts or answer evidence')
    if migration.get('attempt_ids') != carried:
        raise ValueError('Historical review migration evidence scope differs')
