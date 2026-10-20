"""Require inspectable task and answer evidence when a verdict overrides review."""
import json
from harness.record_io import canonical_json_sha256

from .publication_privacy import read_evidence_binding, evidence_excerpt_matches


def requires_adjudication(row):
    final = row.get('final_verdict')
    return final is not None and any(
        row.get(key) not in {None, final} for key in ('agent_verdict', 'effective_agent_verdict'))


def current_rule_outcomes(case, record):
    """Expose only declared, computed rules as structured adjudication facts."""
    from lexbench_webfetch.metrics import task_passed
    m = record['metrics']
    outcomes = {'task_result': {'passed': task_passed(record['ok'], m)},
                'request_status': {'passed': bool(m.get('request_ok', record['ok']))}}
    if case.get('required_facts'):
        outcomes['required_facts'] = {
            'passed': m['fact_coverage'] >= case.get('fact_coverage_threshold', 1.0),
            'matched': m['fact_hits'], 'total': m['fact_total']}
    if case.get('expected_any'):
        outcomes['expected_any'] = {'passed': bool(m.get('matched_markers'))}
    if case.get('min_content_chars') is not None:
        outcomes['min_content_chars'] = {'passed': m['content_chars'] >= case['min_content_chars'],
            'actual': m['content_chars'], 'minimum': case['min_content_chars']}
    if case.get('body_requirements'):
        outcomes['body_requirements'] = {'passed': m['body_coverage_pass'],
            'coverage': [{'id': r['id'], 'coverage': r['coverage'],
                          'target_pass': r['target_pass'], 'floor_pass': r['floor_pass']}
                         for r in m['body_region_results']]}
    if case.get('required_patterns'):
        outcomes['required_patterns'] = {'passed': all(r['pass'] for r in m['pattern_results']),
            'results': m['pattern_results']}
    if case.get('acceptance_contract'):
        outcomes['acceptance_contract'] = {
            'passed': m['stable_task_pass'],
            'anchor_hits': m['stable_anchor_hits'], 'anchor_total': m['stable_anchor_total'],
            'missing_anchors': [r['id'] for r in m['stable_anchor_results'] if not r['pass']],
            'records': m['stable_unique_record_count'], 'minimum_records': m['stable_minimum_records']}
    if case.get('composite_acceptance'):
        outcomes['composite_acceptance'] = {'passed': m['composite_acceptance_pass']}
    if case.get('expected_resources'):
        outcomes['expected_resources'] = {'passed': m['resource_pass']}
    if case.get('forbidden_any'):
        outcomes['forbidden_any'] = {'passed': not m['forbidden_requirement_hits'],
                                    'hits': m['forbidden_requirement_hits']}
    if case.get('diagnostic_any'):
        hits = m['diagnostic_hits']
        if any(hit not in case['diagnostic_any'] for hit in hits):
            raise ValueError('Diagnostic hits do not belong to the declared case')
        outcomes['diagnostic_any'] = {'hits': list(hits), 'affects_verdict': False}
    return outcomes


def adjudication_reason(assertions):
    """Render claims from checked facts, never unchecked free-form hit text."""
    labels = {'task_result': '任务判定', 'request_status': '请求状态',
        'required_facts': '必需文字', 'expected_any': '目标标记',
        'min_content_chars': '最低长度', 'body_requirements': '正文覆盖',
        'required_patterns': '格式与代码要求', 'acceptance_contract': '内容与条目要求',
        'composite_acceptance': '组合要求', 'expected_resources': '目标资源',
        'forbidden_any': '明确禁止要求'}
    parts = []
    for key, value in assertions.items():
        if key == 'diagnostic_any':
            parts.append('诊断词命中：' + ('、'.join(value['hits']) or '无') + '（不单独决定通过或失败）')
        else:
            detail = ''
            if key == 'required_facts':
                detail = f"（{value['matched']}/{value['total']}）"
            elif key == 'min_content_chars':
                detail = f"（{value['actual']}，要求至少 {value['minimum']}）"
            elif key == 'acceptance_contract':
                detail = (f"（稳定文字 {value['anchor_hits']}/{value['anchor_total']}；"
                          f"独立条目 {value['records']}，要求至少 {value['minimum_records']}"
                          + ('；缺少 ' + '、'.join(value['missing_anchors'])
                             if value['missing_anchors'] else '') + '）')
            elif key == 'required_patterns':
                missing = [r['id'] for r in value['results'] if not r['pass']]
                detail = ('（未满足 ' + '、'.join(missing) + '）') if missing else ''
            parts.append(labels[key] + '：' + ('通过' if value['passed'] else '未通过') + detail)
    return '；'.join(parts) + '。'


def require_rule_assertions(case, record, row, proof):
    available = current_rule_outcomes(case, record)
    identifiers = proof.get('rule_ids', [])
    if (not identifiers or len(identifiers) != len(set(identifiers))
            or any(key not in available for key in identifiers)
            or 'task_result' not in identifiers):
        raise ValueError('Adjudication cites an unknown or missing task rule')
    expected = {key: available[key] for key in identifiers}
    if proof.get('rule_assertions') != expected:
        raise ValueError('Adjudication rule claims differ from current metrics')
    final = 'pass' if available['task_result']['passed'] else 'fail'
    if row.get('final_verdict') != final:
        raise ValueError('Adjudication verdict contradicts computed task rules')
    reason = adjudication_reason(expected)
    if proof.get('reason') != reason or row.get('adjudication') != reason:
        raise ValueError('Adjudication reason differs from verified rule claims')


def require_adjudication_evidence(source, dataset, records, review):
    require_retained_reviews(source, dataset, records, review)
    _require_supplemental_reviews(source, dataset, records, review)
    if review.get('rule_binding_revision'):
        raise ValueError('Historical review migrations are not current-contract review')
    cases = {case['id']: case for case in dataset.get('cases', [])}
    indexed = {record['attempt_id']: record for record in records}
    checked = 0
    for row in review.get('attempts', []):
        if not requires_adjudication(row):
            continue
        record = indexed[row['attempt_id']]
        proof = row.get('adjudication_evidence', {})
        reason = proof.get('reason', '').strip()
        if (not reason
                or proof.get('case_sha256') != canonical_json_sha256(cases[record['case_id']])
                or proof.get('content_sha256') != record['metrics']['content_sha256']
                or not proof.get('rule_ids')):
            raise ValueError('Overridden review lacks specific rule/evidence: ' + row['attempt_id'])
        require_rule_assertions(cases[record['case_id']], record, row, proof)
        answer_binding = {'path': record['artifact_path'], 'sha256': proof['content_sha256']}
        raw = read_evidence_binding(source, answer_binding).read_bytes()
        text = raw.decode('utf-8', errors='replace')
        samples = proof.get('samples', [])
        if not text:
            if proof.get('empty_output') is not True:
                raise ValueError('Empty adjudication answer must be explicit')
        elif not samples:
            raise ValueError('Adjudication lacks an answer excerpt')
        for sample in samples:
            if not evidence_excerpt_matches(source, answer_binding, sample, text):
                raise ValueError('Adjudication excerpt differs from original answer')
        checked += 1
    return checked


def require_current_review_state(records, review):
    from lexbench_webfetch.metrics import task_measured, task_passed
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


def _require_supplemental_reviews(source, dataset, records, review):
    """Bind newly reviewed answers without rewriting inherited opinions."""
    from .publication_privacy import read_evidence_binding
    fresh = {r['attempt_id'] for r in review.get('attempts', [])
             if r.get('review_origin') == 'fresh'}
    if not review.get('supplemental_reviews'):
        if fresh:
            raise ValueError('Fresh reviews require their supplemental evidence')
        return set()
    indexed = {row['attempt_id']: row for row in records}
    cases = {case['id']: case for case in dataset['cases']}
    current = {row['attempt_id']: row for row in review.get('attempts', [])}
    seen = set()
    for binding in review.get('supplemental_reviews', []):
        supplement = json.loads(read_evidence_binding(source, binding).read_text())
        rows = supplement.get('attempts', [])
        identifiers = [row['attempt_id'] for row in rows]
        if (supplement.get('kind') != 'supplemental_saved_output_review'
                or supplement.get('results_sha256') != review.get('results_sha256')
                or supplement.get('provider_calls_added') != 0
                or not identifiers or len(set(identifiers)) != len(identifiers)
                or seen.intersection(identifiers)
                or supplement.get('reviewed_attempts') != len(rows)
                or not supplement.get('reviewer')
                or 'blind_initial_verdict' not in supplement.get('review_protocol', {})):
            raise ValueError('Supplemental review scope or provenance differs')
        initial = {}
        if supplement.get('review_protocol', {}).get('blind_initial_verdict') is True:
            for output_binding in supplement.get('blind_initial_outputs', []):
                output = json.loads(read_evidence_binding(source, output_binding).read_text())
                if output.get('machine_judgments_withheld') is not True:
                    raise ValueError('Fresh initial review was not blind')
                for opinion in output.get('attempts', []):
                    identifier = opinion['attempt_id']
                    if identifier in initial:
                        raise ValueError('Fresh initial review repeats an attempt')
                    initial[identifier] = opinion
            if not set(identifiers).issubset(initial):
                raise ValueError('Fresh review lacks its immutable initial opinions')
        for row in rows:
            if initial:
                opinion = initial[row['attempt_id']]
                if (row.get('agent_verdict') != opinion.get('verdict')
                        or row.get('agent_evidence') != opinion.get('reason')
                        or row.get('reviewer_anomalies') != opinion.get('anomalies')):
                    raise ValueError('Fresh initial review opinion changed')
            record = indexed.get(row['attempt_id'])
            if record is None or current.get(row['attempt_id']) != row:
                raise ValueError('Supplemental review opinions changed')
            proof = row.get('review_evidence', {})
            if (proof.get('case_sha256') != canonical_json_sha256(cases[record['case_id']])
                    or row.get('content_sha256') != record['metrics']['content_sha256']
                    or not row.get('agent_evidence', '').strip()
                    or row.get('agent_verdict') not in {'pass', 'fail', 'insufficient'}):
                raise ValueError('Supplemental review lacks bound task and answer evidence')
            answer_binding = {'path': record['artifact_path'], 'sha256': row['content_sha256']}
            raw = read_evidence_binding(source, answer_binding).read_bytes()
            text = raw.decode('utf-8', errors='replace')
            if text and not proof.get('samples'):
                raise ValueError('Supplemental review lacks an answer excerpt')
            for sample in proof.get('samples', []):
                if not evidence_excerpt_matches(source, answer_binding, sample, text):
                    raise ValueError('Supplemental review excerpt differs')
        seen.update(identifiers)
    if fresh and seen != fresh:
        raise ValueError('Fresh review coverage differs from supplemental evidence')
    return seen




def require_retained_reviews(source, dataset, records, review):
    """Retain actual old opinions only when their decision inputs still suffice.

    A removed, unmatched ban does not invalidate an otherwise identical review.
    A hit, a dependent old explanation, changed positive rule, changed answer or
    changed verdict requires new review. Retention never becomes a fresh review.
    """
    binding = review.get('retained_reviews')
    retained = {r['attempt_id'] for r in review.get('attempts', [])
                if r.get('review_origin') == 'retained'}
    if not binding:
        if retained:
            raise ValueError('Retained reviews require their original evidence')
        return set()
    from .publication_privacy import read_evidence_binding
    from .result_review import review_input_identity
    from harness.record_io import load_jsonl
    receipt = json.loads(read_evidence_binding(source, binding).read_text())
    if receipt.get('kind') == 'current_retained_review_dependencies':
        return _require_current_retained_reviews(source, dataset, records, review, receipt)
    prior = json.loads(read_evidence_binding(source, receipt['prior_review']).read_text())
    before = json.loads(read_evidence_binding(source, receipt['prior_dataset']).read_text())
    previous = load_jsonl(read_evidence_binding(source, receipt['prior_results']))
    ids = receipt.get('attempt_ids', [])
    if (receipt.get('kind') != 'unchanged_review_dependency_retention'
            or receipt.get('provider_calls_added') != 0
            or len(ids) != len(set(ids)) or set(ids) != retained
            or review.get('retained_reviewed_attempts') != len(ids)
            or prior.get('rule_binding_revision')
            or prior.get('results_sha256') != receipt['prior_results']['sha256']):
        raise ValueError('Retained review scope or original provenance differs')
    old_cases = {c['id']: c for c in before['cases']}
    cases = {c['id']: c for c in dataset['cases']}
    old_records = {r['attempt_id']: r for r in previous}
    indexed = {r['attempt_id']: r for r in records}
    opinions = {r['attempt_id']: r for r in prior['attempts']}
    current = {r['attempt_id']: r for r in review['attempts']}
    immutable = ('agent_verdict', 'agent_evidence', 'effective_agent_verdict',
                 'effective_agent_evidence', 'initial_review_sha256', 'content_sha256')
    for identifier in ids:
        if identifier not in indexed or identifier not in old_records or identifier not in opinions:
            raise ValueError('Retained review has no original attempt')
        record, old_record = indexed[identifier], old_records[identifier]
        case, old_case = cases[record['case_id']], old_cases[old_record['case_id']]
        # Empty removed fields are immaterial; every positive requirement and
        # every other task field must remain byte-equivalent as JSON values.
        old_bans, bans = old_case.get('forbidden_any', []), case.get('forbidden_any', [])
        old_hints, hints = old_case.get('diagnostic_any', []), case.get('diagnostic_any', [])
        ignored = {'forbidden_any', 'diagnostic_any'}
        if ({k: v for k, v in old_case.items() if k not in ignored}
                != {k: v for k, v in case.items() if k not in ignored}
                or not ((old_bans == bans and old_hints == hints)
                        or (not bans and hints == old_bans and not old_hints))):
            raise ValueError('Retained review has changed positive requirements')
        opinion, row = opinions[identifier], current[identifier]
        cited = set(opinion.get('adjudication_evidence', {}).get('rule_ids', []))
        if ((old_bans != bans or old_hints != hints)
                and (record['metrics'].get('diagnostic_hits')
                     or cited & ignored)):
            raise ValueError('Retained review depends on the changed text policy')
        if (review_input_identity(record) != review_input_identity(old_record)
                or any(row.get(k) != opinion.get(k) for k in immutable)
                or opinion.get('final_verdict') not in {'pass', 'fail'}
                or row.get('final_verdict') != opinion['final_verdict']):
            raise ValueError('Retained review answer, opinion or verdict changed')
        read_evidence_binding(source, {'path': record['artifact_path'],
                                       'sha256': row.get('content_sha256')})
    fresh = sum(r.get('review_origin') == 'fresh' for r in review['attempts'])
    if (review.get('fresh_reviewed_attempts') != fresh
            or fresh + len(ids) != len(review['attempts'])):
        raise ValueError('Fresh and retained review counts differ')
    return retained


def compact_retained_review_dependencies(source, dataset, records, review):
    """Keep actual reused opinions and decision identities, not old matrices."""
    require_retained_reviews(source, dataset, records, review)
    from .publication_privacy import read_evidence_binding
    from .result_review import review_input_identity
    receipt = json.loads(read_evidence_binding(source, review['retained_reviews']).read_text())
    if receipt.get('kind') == 'current_retained_review_dependencies':
        return receipt
    prior = json.loads(read_evidence_binding(source, receipt['prior_review']).read_text())
    before = json.loads(read_evidence_binding(source, receipt['prior_dataset']).read_text())
    from harness.record_io import load_jsonl
    previous = {r['attempt_id']: r for r in load_jsonl(read_evidence_binding(source, receipt['prior_results']))}
    old_cases = {c['id']: c for c in before['cases']}
    cases = {c['id']: c for c in dataset['cases']}
    indexed = {r['attempt_id']: r for r in records}
    opinions = {r['attempt_id']: r for r in prior['attempts']}
    rows = []
    for identifier in receipt['attempt_ids']:
        old = previous[identifier]
        case = old_cases[old['case_id']]
        rows.append({'attempt_id': identifier,
            'case_sha256': canonical_json_sha256(cases[indexed[identifier]['case_id']]),
            'original_positive_case_sha256': canonical_json_sha256({k: v for k, v in case.items()
                if k not in {'forbidden_any', 'diagnostic_any'}}),
            'original_forbidden_any': case.get('forbidden_any', []),
            'original_diagnostic_any': case.get('diagnostic_any', []),
            'original_review_input': review_input_identity(old),
            'current_review_input': review_input_identity(indexed[identifier]),
            'original_opinion_sha256': canonical_json_sha256(opinions[identifier]),
            'original_opinion': opinions[identifier]})
    result = {'kind': 'current_retained_review_dependencies', 'schema_version': 1,
        'provider_calls_added': 0, 'new_independent_reviews': 0,
        'validation_scope': 'current_decision_dependencies', 'attempts': rows}
    _require_current_retained_reviews(source, dataset, records, review, result)
    return result


def _require_current_retained_reviews(source, dataset, records, review, receipt):
    from .result_review import review_input_identity
    rows = receipt.get('attempts', [])
    dependencies = {r['attempt_id']: r for r in rows}
    retained = {r['attempt_id'] for r in review.get('attempts', [])
                if r.get('review_origin') == 'retained'}
    if (receipt.get('schema_version') != 1 or receipt.get('provider_calls_added') != 0
            or receipt.get('new_independent_reviews') != 0
            or receipt.get('validation_scope') != 'current_decision_dependencies'
            or len(dependencies) != len(rows) or set(dependencies) != retained
            or review.get('retained_reviewed_attempts') != len(rows)):
        raise ValueError('Compact retained review scope differs')
    cases = {c['id']: c for c in dataset['cases']}
    indexed = {r['attempt_id']: r for r in records}
    current = {r['attempt_id']: r for r in review['attempts']}
    immutable = ('agent_verdict', 'agent_evidence', 'effective_agent_verdict',
                 'effective_agent_evidence', 'initial_review_sha256', 'content_sha256')
    for identifier, dependency in dependencies.items():
        record = indexed[identifier]
        case = cases[record['case_id']]
        opinion, row = dependency['original_opinion'], current[identifier]
        old_bans, old_hints = (dependency['original_forbidden_any'],
                               dependency['original_diagnostic_any'])
        bans, hints = case.get('forbidden_any', []), case.get('diagnostic_any', [])
        if (dependency['case_sha256'] != canonical_json_sha256(case)
                or dependency['original_positive_case_sha256'] != canonical_json_sha256({k: v
                    for k, v in case.items() if k not in {'forbidden_any', 'diagnostic_any'}})
                or not ((old_bans == bans and old_hints == hints)
                        or (not bans and hints == old_bans and not old_hints))):
            raise ValueError('Compact retained review contract changed')
        cited = set(opinion.get('adjudication_evidence', {}).get('rule_ids', []))
        if ((old_bans != bans or old_hints != hints)
                and (record['metrics'].get('diagnostic_hits')
                     or cited & {'forbidden_any', 'diagnostic_any'})):
            raise ValueError('Compact retained opinion depends on changed policy')
        identity = review_input_identity(record)
        if (dependency['current_review_input'] != identity
                or dependency['original_review_input'] != identity
                or dependency.get('original_opinion_sha256') != canonical_json_sha256(opinion)
                or opinion.get('attempt_id') != identifier
                or any(row.get(k) != opinion.get(k) for k in immutable)
                or opinion.get('final_verdict') not in {'pass', 'fail'}
                or row.get('final_verdict') != opinion['final_verdict']):
            raise ValueError('Compact retained review input or original opinion changed')
        # Opinion reuse binds the exact original/current decision inputs. The
        # snapshot authority separately validates private diagnostic identities
        # against their disclosed public runtime views; those views are not the
        # original diagnostic bytes and must not be read as if they were.
        read_evidence_binding(source, {'path': record['artifact_path'],
                                      'sha256': record['metrics']['content_sha256']})
        for artifact in identity['raw_artifacts']:
            read_evidence_binding(source, artifact)
    fresh = sum(r.get('review_origin') == 'fresh' for r in review['attempts'])
    if (review.get('fresh_reviewed_attempts') != fresh
            or fresh + len(rows) != len(review['attempts'])):
        raise ValueError('Fresh and compact retained review counts differ')
    return retained
