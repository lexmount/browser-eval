"""Require inspectable task and answer evidence when a verdict overrides review."""
import json
import hashlib
from dataclasses import dataclass
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.metrics import task_passed
from .local_execution_contract import local_measurement_call_sha256
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


def adjudication_reason(assertions, *, language="zh"):
    """Render checked facts through one rule traversal with localized messages."""
    if language not in {"zh", "en"}:
        raise ValueError("Adjudication language must be zh or en")
    labels = {
        'zh': {'task_result': '任务判定', 'request_status': '请求状态',
            'required_facts': '必需文字', 'expected_any': '目标标记',
            'min_content_chars': '最低长度', 'body_requirements': '正文覆盖',
            'required_patterns': '格式与代码要求', 'acceptance_contract': '内容与条目要求',
            'composite_acceptance': '组合要求', 'expected_resources': '目标资源',
            'forbidden_any': '明确禁止要求'},
        'en': {'task_result': 'Task result', 'request_status': 'Request status',
            'required_facts': 'Required text', 'expected_any': 'Target markers',
            'min_content_chars': 'Minimum length', 'body_requirements': 'Body coverage',
            'required_patterns': 'Formatting and code requirements',
            'acceptance_contract': 'Content and record requirements',
            'composite_acceptance': 'Combined requirements',
            'expected_resources': 'Target resources', 'forbidden_any': 'Prohibited content'},
    }[language]
    messages = {
        'zh': {
            'list_separator': '、', 'sentence_separator': '；', 'end': '。',
            'pass': '通过', 'fail': '未通过', 'none': '无',
            'result': '{label}：{verdict}{detail}',
            'diagnostic': '诊断词命中：{hits}（不单独决定通过或失败）',
            'required_facts': '（{matched}/{total}）',
            'min_content_chars': '（{actual}，要求至少 {minimum}）',
            'acceptance_contract': ('（稳定文字 {anchor_hits}/{anchor_total}；'
                                    '独立条目 {records}，要求至少 {minimum_records}{missing}）'),
            'missing': '；缺少 {items}', 'required_patterns': '（未满足 {items}）',
        },
        'en': {
            'list_separator': ', ', 'sentence_separator': '; ', 'end': '.',
            'pass': 'pass', 'fail': 'fail', 'none': 'none',
            'result': '{label}: {verdict}{detail}',
            'diagnostic': 'Diagnostic matches: {hits} (do not independently determine pass or fail)',
            'required_facts': ' ({matched}/{total})',
            'min_content_chars': ' ({actual}; minimum {minimum})',
            'acceptance_contract': (' (stable text {anchor_hits}/{anchor_total}; '
                                    'distinct records {records}; minimum {minimum_records}{missing})'),
            'missing': '; missing {items}', 'required_patterns': ' (unmet: {items})',
        },
    }[language]
    parts = []
    for key, value in assertions.items():
        if key == 'diagnostic_any':
            hits = messages['list_separator'].join(value['hits']) or messages['none']
            parts.append(messages['diagnostic'].format(hits=hits))
            continue
        detail = ''
        if key in {'required_facts', 'min_content_chars'}:
            detail = messages[key].format(**value)
        elif key == 'acceptance_contract':
            missing = messages['list_separator'].join(value['missing_anchors'])
            missing = messages['missing'].format(items=missing) if missing else ''
            detail = messages[key].format(missing=missing, **value)
        elif key == 'required_patterns':
            missing = messages['list_separator'].join(r['id'] for r in value['results'] if not r['pass'])
            detail = messages[key].format(items=missing) if missing else ''
        parts.append(messages['result'].format(
            label=labels[key], verdict=messages['pass' if value['passed'] else 'fail'], detail=detail))
    return messages['sentence_separator'].join(parts) + messages['end']


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


def score_reviewed_page_identity(source, case, record, annotation, captures, *, historical_scoring_source=None):
    """Apply a bound retrospective content correspondence, never a native URL."""
    from lexbench_webfetch.metrics import normalize_for_match, task_measured
    from lexbench_webfetch.scoring import score_attempt, _same_page_url, SCORING_SOURCE_SHA256

    if (annotation.get("attempt_id") != record["attempt_id"]
            or annotation.get("case_sha256") != canonical_json_sha256(case)
            or annotation.get("call_sha256") != local_measurement_call_sha256(record)
            or annotation.get("content_sha256") != record["metrics"]["content_sha256"]
            or record.get("final_url") is not None
            or annotation.get("basis") != "retrospective_content_correspondence"
            or not annotation.get("reviewed_at")):
        raise ValueError("Page review lost its case, answer or physical call binding")
    historical = historical_scoring_source is not None and historical_scoring_source != SCORING_SOURCE_SHA256
    original_metrics = None if historical else score_attempt(
        case, contained_path(source, record["artifact_path"]).read_bytes(),
        ok=record["ok"], metadata=record.get("provider_metadata"),
        previous_metrics=record["metrics"], status_code=record.get("status_code"),
        title=record.get("title"), final_url=record.get("final_url"),
    )
    if not historical and (task_measured(original_metrics)
            or original_metrics.get("page_identity_status") != "unavailable"):
        raise ValueError("Page review may only fill an unavailable page identity")
    capture = captures[annotation["capture_index"]]
    corresponding_url = annotation["corresponding_url"]
    if (capture.get("url") != corresponding_url
            or canonical_json_sha256(capture) != annotation.get("capture_sha256")):
        raise ValueError("Page review capture binding differs")
    answer = contained_path(source, record["artifact_path"]).read_bytes()
    if hashlib.sha256(answer).hexdigest() != annotation["content_sha256"]:
        raise ValueError("Page review answer changed")
    samples = annotation.get("matching_samples", [])
    saved = normalize_for_match(answer.decode("utf-8"))
    observed = normalize_for_match(capture["text"])
    normalized = [normalize_for_match(value).strip() for value in samples if isinstance(value, str)]
    strong_samples = {value for value in normalized if sum(char.isalnum() for char in value) >= 20}
    if (len(normalized) != len(samples) or len(strong_samples) < 2
            or any(not any(char.isalnum() for char in value)
                   or value not in saved or value not in observed for value in normalized)):
        raise ValueError("Page review needs matching substantive text in both bodies")
    navigation = (case.get("source_benchmark", {}).get("risk_control", {})
                  .get("navigation_contract", {}))
    acceptable = [navigation.get("requested_url"), navigation.get("observed_final_url")]
    if not any(value and _same_page_url(corresponding_url, value) for value in acceptable):
        raise ValueError("Reviewed page does not satisfy the frozen URL requirement")
    if historical:
        # The revision chain already hash-verifies these historical results.
        # Validate their review evidence without replaying a different scorer
        # and pretending its metrics were produced by the historical version.
        metrics = record['metrics']
        if (metrics.get('scoring_source_sha256') != historical_scoring_source
                or metrics.get('page_identity_required') is not True
                or metrics.get('page_identity_status') != 'verified'
                or metrics.get('final_url_consistent') is not True
                or metrics.get('content_chars') != len(answer.decode('utf-8'))
                or metrics.get('page_identity_review_sha256') != canonical_json_sha256(annotation)):
            raise ValueError('Reviewed result differs from its bound historical shared scorer')
        return dict(metrics)
    metrics = score_attempt(
        case, answer, ok=record["ok"], metadata=record.get("provider_metadata"),
        previous_metrics=record["metrics"], status_code=record.get("status_code"),
        title=record.get("title"), final_url=corresponding_url,
    )
    metrics["page_identity_basis"] = "retrospective_content_correspondence"
    metrics["page_identity_review_sha256"] = canonical_json_sha256(annotation)
    return metrics


def _require_page_identity_review(source, dataset, records, *, scoring_sources=None):
    binding = dataset.get("comparison", {}).get("page_identity_review")
    marked = {r["attempt_id"] for r in records
              if r["metrics"].get("page_identity_basis") == "retrospective_content_correspondence"}
    if binding is None:
        if marked:
            raise ValueError("Reviewed page identities lack a receipt")
        return
    try:
        path = read_evidence_binding(source, binding)
    except ValueError as error:
        raise ValueError("Page identity review receipt changed") from error
    receipt = json.loads(path.read_text())
    inputs = PageIdentityReviewInputs.parse(receipt)
    try:
        capture_path = read_evidence_binding(source, {
            "path": inputs.captures_path, "sha256": inputs.captures_sha256,
        }, allow_public_view=False)
    except ValueError as error:
        raise ValueError("Page review captures changed") from error
    captures = json.loads(capture_path.read_text())
    inputs.require_captures(captures)
    _require_identity_binding_revision(source, dataset, receipt)
    annotations = receipt["attempts"]
    if (receipt.get("kind") != "retrospective_page_identity_review"
            or receipt.get("provider_calls_added") != 0
            or len(annotations) != len(marked)
            or {a["attempt_id"] for a in annotations} != marked):
        raise ValueError("Page review scope differs from annotated results")
    cases = {case["id"]: case for case in dataset["cases"]}
    indexed = {r["attempt_id"]: r for r in records}
    for annotation in annotations:
        record = indexed[annotation["attempt_id"]]
        declared = None
        if scoring_sources is not None:
            declared = scoring_sources.get(record["attempt_id"])
            if not declared or record["metrics"].get("scoring_source_sha256") != declared:
                raise ValueError("Reviewed result lost its revision scoring source")
        expected = score_reviewed_page_identity(
            source, cases[record["case_id"]], record, annotation, captures,
            historical_scoring_source=declared)
        if expected != record["metrics"]:
            raise ValueError("Reviewed result differs from the shared scorer")


def _require_local_review(source, dataset, records, *, review_document=None, scoring_sources=None):
    """Bind optional full independent review without certifying local evidence."""
    _require_page_identity_review(source, dataset, records, scoring_sources=scoring_sources)
    binding = dataset.get("comparison", {}).get("independent_review")
    if binding is None and review_document is None:
        return None
    if review_document is None:
        try:
            path = read_evidence_binding(source, binding, allow_public_view=False)
        except ValueError as error:
            raise ValueError("Local independent review binding changed") from error
        review = json.loads(path.read_text())
    else:
        review = review_document
    rows = review.get("attempts", [])
    indexed = {row["attempt_id"]: row for row in rows}
    expected = {row["attempt_id"]: row for row in records}
    if (review.get("kind") != "local_independent_review"
            or review.get("review_protocol", {}).get("scope") != "all"
            or review.get("reviewed_attempts") != len(records)
            or review.get("expected_attempts") != len(records)
            or review.get("results_sha256") != hashlib.sha256((source / "results.jsonl").read_bytes()).hexdigest()
            or len(indexed) != len(rows) or set(indexed) != set(expected)):
        raise ValueError("Local independent review scope differs")
    unresolved = 0
    from lexbench_webfetch.metrics import task_measured
    for identifier, row in indexed.items():
        record = expected[identifier]
        verdict = ("unavailable" if not task_measured(record["metrics"]) else
                   "pass" if task_passed(record["ok"], record["metrics"]) else "fail")
        if (row.get("content_sha256") != record["metrics"]["content_sha256"]
                or row.get("current_verdict") != verdict
                or row.get("agent_verdict") not in {"pass", "fail", "insufficient"}
                or row.get("final_verdict") not in {verdict, None}):
            raise ValueError("Local review differs from scored evidence")
        if row.get("final_verdict") is None:
            unresolved += 1
            if row.get("status") != "deferred_unadjudicated":
                raise ValueError("Local unresolved review lacks explicit deferral")
    if review.get("unresolved") != unresolved:
        raise ValueError("Local review unresolved count differs")
    require_adjudication_evidence(source, dataset, records, review)
    return review


def _legacy_diagnostic_migrated_case(case):
    """Decode the exact task transition in retained legacy review receipts."""
    import copy
    result = copy.deepcopy(case)
    hints = result.pop('forbidden_any', [])
    if hints:
        result['diagnostic_any'] = list(dict.fromkeys(result.get('diagnostic_any', []) + hints))
    result.pop('acceptance_policy_version', None)
    return result


def _require_identity_binding_revision(source, dataset, receipt):
    """Verify a saved migration receipt; never create a new migrated review."""
    migration = receipt.get('binding_revision')
    if not migration:
        return
    previous = json.loads(read_evidence_binding(source, migration['prior_receipt']).read_text())
    old_cases = json.loads(read_evidence_binding(source, migration['prior_cases']).read_text())
    cases = {case['id']: case for case in dataset['cases']}
    if (migration.get('kind') != 'diagnostic_field_migration'
            or migration.get('new_independent_review') is not False
            or receipt.get('captures_path') != previous.get('captures_path')
            or receipt.get('captures_sha256') != previous.get('captures_sha256')):
        raise ValueError('Page identity evidence migration changed source observations')
    annotations = []
    prior_ids = {canonical_json_sha256(case): identifier for identifier, case in old_cases.items()}
    for annotation in previous['attempts']:
        identifier = prior_ids.get(annotation['case_sha256'])
        if identifier is None or identifier not in cases:
            raise ValueError('Page identity migration has no matching prior task')
        old = old_cases[identifier]
        migrated = _legacy_diagnostic_migrated_case(old)
        # Older compiled navigation tasks implied identity acceptance. Keep
        # that requirement explicit when adopting the diagnostic default.
        if (cases[identifier].get('page_identity_required') is True
                and 'page_identity_required' not in old):
            navigation = old.get('source_benchmark', {}).get('risk_control', {}).get('navigation_contract', {})
            dynamic_identity = (old.get('track') == 'dynamic' and old.get('evaluation_url')
                                and old.get('expected_final_url') and old.get('target_language')
                                and old.get('target_region'))
            if ((old.get('track') == 'risk_control' and navigation.get('requested_url')
                 and navigation.get('observed_final_url')) or dynamic_identity):
                migrated['page_identity_required'] = True
        if (annotation['case_sha256'] != canonical_json_sha256(old)
                or migrated != cases[identifier]):
            raise ValueError('Page identity migration changed its target contract')
        annotations.append({**annotation, 'prior_case_sha256': annotation['case_sha256'],
                            'case_sha256': canonical_json_sha256(cases[identifier])})
    if receipt['attempts'] != annotations:
        raise ValueError('Page identity migration changed an existing reviewed annotation')


@dataclass(frozen=True)
class ReviewedPageIdentityUpdates:
    binding: dict
    metrics_by_attempt: dict


@dataclass(frozen=True)
class PageIdentityAnnotationInput:
    attempt_id: str
    case_sha256: str
    call_sha256: str
    content_sha256: str
    capture_index: int


@dataclass(frozen=True)
class PageIdentityReviewInputs:
    """Typed receipt inputs; retain the original JSON separately for its hashes."""
    captures_path: str
    captures_sha256: str
    attempts: tuple[PageIdentityAnnotationInput, ...]

    @classmethod
    def parse(cls, document):
        if not isinstance(document, dict):
            raise ValueError("page_identity_review must be an object")

        def text(value, key, location):
            item = value.get(key)
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{location}.{key} must be nonempty text")
            return item

        location = "page_identity_review"
        path = text(document, "captures_path", location)
        digest = text(document, "captures_sha256", location)
        rows = document.get("attempts")
        if not isinstance(rows, list):
            raise ValueError(f"{location}.attempts must be a list")
        parsed, seen = [], set()
        for index, row in enumerate(rows):
            field = f"{location}.attempts[{index}]"
            if not isinstance(row, dict):
                raise ValueError(f"{field} must be an object")
            values = [text(row, key, field) for key in
                      ("attempt_id", "case_sha256", "call_sha256", "content_sha256")]
            if values[0] in seen:
                raise ValueError(f"{field}.attempt_id must be unique")
            seen.add(values[0])
            capture_index = row.get("capture_index")
            if type(capture_index) is not int or capture_index < 0:
                raise ValueError(f"{field}.capture_index must be a nonnegative integer")
            for key in ("capture_sha256", "corresponding_url", "basis", "reviewed_at"):
                text(row, key, field)
            samples = row.get("matching_samples")
            if not isinstance(samples, list) or any(not isinstance(item, str) for item in samples):
                raise ValueError(f"{field}.matching_samples must be a list of text")
            parsed.append(PageIdentityAnnotationInput(*values, capture_index))
        return cls(path, digest, tuple(parsed))

    def require_captures(self, captures):
        if not isinstance(captures, list):
            raise ValueError("page_identity_review.captures must be a list")
        for index, annotation in enumerate(self.attempts):
            if annotation.capture_index >= len(captures):
                raise ValueError(
                    f"page_identity_review.attempts[{index}].capture_index is outside captures")
            capture = captures[annotation.capture_index]
            location = f"page_identity_review.captures[{annotation.capture_index}]"
            if not isinstance(capture, dict):
                raise ValueError(f"{location} must be an object")
            for key in ("url", "text"):
                if not isinstance(capture.get(key), str):
                    raise ValueError(f"{location}.{key} must be text")


def prepare_reviewed_page_identities(source, before_dataset, dataset, records):
    """Validate and return unchanged identity evidence without mutating inputs.

    Old migration receipts remain read-only inputs to the legacy verifier.
    Changed task, answer or physical-call inputs require a new explicit review.
    """
    binding = before_dataset.get('comparison', {}).get('page_identity_review')
    if not binding:
        return None
    original = json.loads(read_evidence_binding(source, binding).read_text())
    inputs = PageIdentityReviewInputs.parse(original)
    try:
        captures_path = read_evidence_binding(source, {
            'path': inputs.captures_path, 'sha256': inputs.captures_sha256,
        }, allow_public_view=False)
    except ValueError as error:
        raise ValueError('Page identity capture changed before rescoring') from error
    captures = json.loads(captures_path.read_text())
    inputs.require_captures(captures)
    cases = {case['id']: case for case in dataset['cases']}
    indexed = {row['attempt_id']: row for row in records}
    receipt = original
    for annotation in inputs.attempts:
        row = indexed.get(annotation.attempt_id)
        case = cases.get(row['case_id']) if row is not None else None
        if (case is None or annotation.case_sha256 != canonical_json_sha256(case)
                or annotation.call_sha256 != local_measurement_call_sha256(row)
                or annotation.content_sha256 != row['metrics']['content_sha256']):
            raise ValueError('Changed page identity review inputs require a new explicit review')
    metrics_by_attempt = {}
    for annotation in receipt['attempts']:
        row = indexed[annotation['attempt_id']]
        metrics_by_attempt[row['attempt_id']] = score_reviewed_page_identity(
            source, cases[row['case_id']], row, annotation, captures)
    proposed_dataset = {**dataset, 'comparison': {
        **dataset.get('comparison', {}), 'page_identity_review': binding}}
    proposed_records = [{**row, 'metrics': metrics_by_attempt.get(row['attempt_id'], row['metrics'])}
                        for row in records]
    _require_page_identity_review(source, proposed_dataset, proposed_records)
    return ReviewedPageIdentityUpdates(dict(binding), metrics_by_attempt)
