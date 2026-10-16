"""Validate task replacements and offline revisions of a bound local snapshot."""
import hashlib
import json
from pathlib import Path

from harness.record_io import load_jsonl

from .cohort_exclusions import require_cohort_exclusions
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.scoring_regressions import require_scoring_regressions
from .scoring_revision import ALLOWED_FIELDS


def read_bound(source, binding):
    from .publication_privacy import read_evidence_binding
    return read_evidence_binding(source, binding)


def require_local_task_replacement(source, binding, dataset, records):
    """Verify complete new task calls while preserving every unrelated saved row."""
    receipt = json.loads(read_bound(source, binding).read_text())
    if receipt.get('kind') != 'local_task_replacement' or not receipt.get('reason'):
        raise ValueError('Task replacement needs a declared reason')
    before = json.loads(read_bound(source, receipt['baseline_dataset']).read_text())
    previous = load_jsonl(read_bound(source, receipt['baseline_results']))
    cohort = json.loads(read_bound(source, receipt['cohort_dataset']).read_text())
    calls = load_jsonl(read_bound(source, receipt['cohort_results']))
    removed, added = receipt['removed_case_id'], receipt['added_case_id']
    old_cases = {c['id']: c for c in before['cases']}
    new_cases = {c['id']: c for c in dataset['cases']}
    if (removed == added or removed not in old_cases or added in old_cases
            or len(old_cases) != len(before['cases']) or len(new_cases) != len(dataset['cases'])
            or set(new_cases) != (set(old_cases) - {removed}) | {added}
            or before.get('coverage_plan') != dataset.get('coverage_plan')
            or any(new_cases[k] != v for k, v in old_cases.items() if k != removed)
            or cohort['cases'] != [new_cases[added]]
            or old_cases[removed].get('track') != new_cases[added].get('track')):
        raise ValueError('Task replacement changed unrelated cases or task family')
    providers = set(before['coverage_plan'][old_cases[removed].get('track', 'web')])
    expected = {(p, added) for p in providers}
    if (len(calls) != len(expected) or receipt.get('provider_calls_added') != len(calls)
            or {(r['provider'], r['case_id']) for r in calls} != expected
            or any(r.get('run') != 1 or r.get('provider_metadata', {}).get('retry') for r in calls)):
        raise ValueError('Task replacement needs one new call from every method')
    inherited = [r for r in previous if r['case_id'] != removed]
    rows = {r['attempt_id']: r for r in records}
    wanted = {r['attempt_id']: r for r in inherited + calls}
    if (len(rows) != len(records) or len(wanted) != len(inherited) + len(calls)
            or len(records) != len(previous) or rows != wanted):
        raise ValueError('Task replacement altered an inherited or newly measured result')
    if any(r['metrics'].get('evaluation_excluded') for r in calls):
        raise ValueError('Replacement task cannot be excluded after seeing its results')
    from .local_audit import require_local_execution_contract
    if not receipt.get('execution_contract') or not receipt.get('cohort_execution'):
        raise ValueError('Task replacement requires its complete execution contract')
    contract = json.loads(read_bound(source, receipt['execution_contract']).read_text())
    execution = json.loads(read_bound(source, receipt['cohort_execution']).read_text())
    if not contract.get('runner_inputs'):
        raise ValueError('Task replacement requires retained runner inputs and source observations')
    require_local_execution_contract(source, cohort, calls, contract, execution)
    return {identifier: row['metrics']['scoring_source_sha256']
            for identifier, row in rows.items()}


def validate_revision_chain(source, binding, dataset=None, records=None):
    """Validate each transition, then expose the earliest replacement inputs."""
    if dataset is None:
        dataset = json.loads((source / 'dataset.json').read_text())
    if records is None:
        records = load_jsonl(source / 'results.jsonl')
    outputs = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
               for name in ('dataset.json', 'results.jsonl')}
    seen, latest_scoring = set(), None
    while True:
        identity = (binding['path'], binding['sha256'])
        if identity in seen:
            raise ValueError('Local score revision lineage repeats a receipt')
        seen.add(identity)
        receipt = json.loads(read_bound(source, binding).read_text())
        expected, before, previous = _require_revision_step(source, receipt, dataset, records, outputs)
        if latest_scoring is None:
            latest_scoring = expected
        parent = receipt.get('predecessor_revision')
        if parent is None:
            return latest_scoring, receipt['input_dataset'], receipt['input_results']
        outputs = {'dataset.json': receipt['input_dataset']['sha256'],
                   'results.jsonl': receipt['input_results']['sha256']}
        dataset, records, binding = before, previous, parent


def require_local_revision(source, binding, dataset, records):
    return validate_revision_chain(source, binding, dataset, records)[0]


def _require_native_answer_correction(source, original, corrected, proof):
    """Verify a derived answer against the same immutable native response."""
    from lexbench_webfetch.answer_projection import project_native_answer, project_native_result_fields
    from .publication_privacy import original_evidence_size
    if proof.get('attempt_id') != original['attempt_id']:
        raise ValueError('Native answer correction lost its attempt identity')
    native = read_bound(source, proof['native_response'])
    metadata = original.get('provider_metadata', {})
    if (proof['native_response']['sha256'] != metadata.get('raw_response_sha256')
            or original_evidence_size(source, proof['native_response']) != metadata.get('raw_response_bytes')):
        raise ValueError('Native answer correction changed its original response')
    before = proof['original_artifact']
    after = proof['corrected_artifact']
    if (before['path'] != original['artifact_path']
            or before['sha256'] != original['metrics']['content_sha256']
            or after['path'] != corrected['artifact_path']
            or after['sha256'] != corrected['metrics']['content_sha256']
            or before['path'] == after['path']):
        raise ValueError('Native answer correction must preserve its old artifact')
    read_bound(source, before)
    payload = json.loads(native.read_bytes())
    text, fields = project_native_answer(payload, proof['projection'])
    result_fields = (project_native_result_fields(payload, proof['projection'], request_url=original.get('url'))
                     if 'result_fields' in proof['projection'] else {})
    if any(corrected.get(key) != value for key, value in result_fields.items()):
        raise ValueError('Native result context correction differs from original fields')
    if read_bound(source, after).read_bytes() != text.encode('utf-8', errors='replace'):
        raise ValueError('Native answer correction differs from declared original fields')
    for field in proof['projection'].get('answer_fields', {}).values():
        if field.get('format') == 'html_attribute_urls' and field.get('base_url') != (result_fields.get('final_url') or original['url']):
            raise ValueError('Native URL projection must use the verified result or request URL')
    expected_metadata = {**metadata, **fields, 'native_answer_projection': proof['projection']}
    if corrected.get('provider_metadata') != expected_metadata:
        raise ValueError('Native answer correction changed unrelated response metadata')
    derived = {'metrics', 'scoring', 'diagnosis', 'failure_category',
               'artifact_path', 'provider_metadata'} | set(result_fields)
    if ({k: v for k, v in original.items() if k not in derived}
            != {k: v for k, v in corrected.items() if k not in derived}):
        raise ValueError('Native answer correction changed physical call facts')


def _require_revision_step(source, receipt, dataset, records, outputs):
    if receipt.get('kind') != 'local_offline_score_revision' or receipt.get('provider_calls_added') != 0:
        raise ValueError('Local revision must preserve original provider calls')
    if (not isinstance(receipt.get('authority', {}).get('description'), str)
            or not receipt['authority']['description'].strip()
            or not isinstance(receipt.get('changed_case_ids'), list)
            or len(receipt['changed_case_ids']) != len(set(receipt['changed_case_ids']))
            or not isinstance(receipt.get('rescored_attempt_ids'), list)
            or len(receipt['rescored_attempt_ids']) != len(set(receipt['rescored_attempt_ids']))
            or not isinstance(receipt.get('task_verdict_changes'), list)):
        raise ValueError('Local revision needs authority, declared scope and verdict changes')
    before_dataset = json.loads(read_bound(source, receipt['input_dataset']).read_text())
    previous = load_jsonl(read_bound(source, receipt['input_results']))
    for name, key in [('dataset.json', 'output_dataset_sha256'), ('results.jsonl', 'output_results_sha256')]:
        if outputs[name] != receipt.get(key):
            raise ValueError('Local revision output binding changed: ' + name)
    regressions = receipt.get('regression_ids')
    if not regressions:
        raise ValueError('Local revision requires executable regression provenance')
    require_scoring_regressions(regressions)
    old_cases = {case['id']: case for case in before_dataset['cases']}
    new_cases = {case['id']: case for case in dataset['cases']}
    if (len(old_cases) != len(before_dataset['cases']) or len(new_cases) != len(dataset['cases'])
            or set(old_cases) != set(new_cases)
            or before_dataset.get('coverage_plan') != dataset.get('coverage_plan')):
        raise ValueError('Local revision changed the original case/method matrix')
    changed_cases = set()
    for identifier, old in old_cases.items():
        new = new_cases[identifier]
        fields = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if fields - (ALLOWED_FIELDS | {'diagnostic_any', 'acceptance_policy_version'}):
            raise ValueError('Local score revision changed source or acquisition inputs')
        if fields:
            changed_cases.add(identifier)
    if changed_cases != set(receipt.get('changed_case_ids', [])):
        raise ValueError('Local revision case scope differs')
    if changed_cases and receipt.get('rule_revision') is not True:
        raise ValueError('Changed acceptance contract requires an explicit rule revision')
    old_rows = {row['attempt_id']: row for row in previous}
    new_rows = {row['attempt_id']: row for row in records}
    if len(old_rows) != len(previous) or len(new_rows) != len(records) or set(old_rows) != set(new_rows):
        raise ValueError('Local revision changed the call inventory')
    scored = set(receipt.get('rescored_attempt_ids', []))
    if not scored <= set(new_rows):
        raise ValueError('Local revision rescoring scope differs')
    native_proofs = receipt.get('native_answer_corrections', [])
    corrected_answers = {item['attempt_id']: item for item in native_proofs}
    if (len(corrected_answers) != len(native_proofs)
            or not set(corrected_answers) <= scored):
        raise ValueError('Native answer correction scope differs from rescoring scope')
    expected_scoring = {}
    for identifier, old in old_rows.items():
        new = new_rows[identifier]
        if identifier in corrected_answers:
            _require_native_answer_correction(source, old, new, corrected_answers[identifier])
        elif ({k: v for k, v in old.items() if k not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
                != {k: v for k, v in new.items() if k not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
                or old['metrics']['content_sha256'] != new['metrics']['content_sha256']):
            raise ValueError('Local revision changed measured call facts: ' + identifier)
        if identifier in scored:
            expected_scoring[identifier] = receipt['scoring_source_sha256']
            if (receipt.get('rule_revision') and
                    new.get('scoring', {}).get('rule_revision') is not True):
                raise ValueError('Scoring policy change cannot be recorded as a parser-only correction')
        else:
            expected_scoring[identifier] = old['metrics']['scoring_source_sha256']
            before = {k: v for k, v in old['metrics'].items() if not k.startswith('evaluation_exclu')}
            after = {k: v for k, v in new['metrics'].items() if not k.startswith('evaluation_exclu')}
            if before != after:
                raise ValueError('Unselected score changed: ' + identifier)
        if new['case_id'] in changed_cases and identifier not in scored:
            raise ValueError('Changed case was not rescored across every method')
    if any(new_rows[key]['metrics'].get('scoring_source_sha256') != value
           for key, value in expected_scoring.items()):
        raise ValueError('Local revision scoring source binding changed')
    from lexbench_webfetch.metrics import task_passed
    if receipt.get('input_verdicts'):
        proof = json.loads(read_bound(source, receipt['input_verdicts']).read_text())
        verdicts = proof.get('verdicts', {})
        if (proof.get('input_results_sha256') != receipt['input_results']['sha256']
                or set(verdicts) != set(old_rows)
                or any(type(value) is not bool for value in verdicts.values())
                or not proof.get('frozen_predicate') or not proof.get('snapshot_manifest')):
            raise ValueError('Frozen input verdict proof is incomplete')
        read_bound(source, proof['frozen_predicate'])
        read_bound(source, proof['snapshot_manifest'])
    else:
        verdicts = {key: task_passed(row['ok'], row['metrics']) for key, row in old_rows.items()}
    transitions = [{'attempt_id': row['attempt_id'], 'before': verdicts[row['attempt_id']],
                    'after': task_passed(row['ok'], row['metrics'])}
                   for row in records if row['attempt_id'] in scored
                   and verdicts[row['attempt_id']] != task_passed(row['ok'], row['metrics'])]
    if transitions != receipt['task_verdict_changes']:
        raise ValueError('Local revision verdict transitions differ from input proof')
    from .local_audit import _require_page_identity_review
    _require_page_identity_review(source, dataset, records, scoring_sources=expected_scoring)
    require_cohort_exclusions(source, dataset, records)
    return expected_scoring, before_dataset, previous


def revise_local_snapshot(source, configuration):
    """Apply declared score-only changes in a separate snapshot, retaining predecessors."""
    import copy
    from datetime import datetime, timezone
    from lexbench_webfetch.scoring import score_attempt, load_list_reference, SCORING_SOURCE_SHA256
    from .cohort_exclusions import apply_cohort_exclusions
    from lexbench_webfetch.metrics import task_passed

    config = json.loads(configuration.read_text())
    regressions = config.get('regression_ids', [])
    require_scoring_regressions(regressions)
    if not regressions or not config.get('authority', {}).get('description'):
        raise ValueError('Local revision requires authority and regression provenance')
    dataset_bytes = (source / 'dataset.json').read_bytes()
    results_bytes = (source / 'results.jsonl').read_bytes()
    input_results_sha256 = hashlib.sha256(results_bytes).hexdigest()
    original_dataset = json.loads(dataset_bytes)
    dataset = copy.deepcopy(original_dataset)
    records = load_jsonl(source / 'results.jsonl')
    replacements = {case['id']: case for case in config['cases']}
    if len(replacements) != len(config['cases']) or not replacements.keys() <= {c['id'] for c in dataset['cases']}:
        raise ValueError('Local revision has an invalid case scope')
    dataset['cases'] = [replacements.get(case['id'], case) for case in dataset['cases']]
    if dataset['cases'] != original_dataset['cases'] and config.get('rule_revision') is not True:
        raise ValueError('Changed acceptance contract requires an explicit rule revision')
    dataset['comparison']['excluded_cases'] = config.get('excluded_cases', [])
    if config.get('rule_revision'):
        dataset['comparison'].pop('scoring_policy_version', None)
        prior_review = dataset['comparison'].pop('independent_review', None)
        if prior_review:
            dataset['comparison']['historical_independent_review'] = prior_review
    additional = set(config.get('rescore_attempt_ids', []))
    if not additional <= {row['attempt_id'] for row in records}:
        raise ValueError('Local revision has an invalid additional rescoring scope')
    revision_key = (input_results_sha256[:16] + '-' +
                    hashlib.sha256(configuration.read_bytes()).hexdigest()[:16])
    history = source / 'local-evidence/offline-revisions' / revision_key
    if history.exists():
        raise ValueError('Local revision already exists; preserve the prior receipt')
    native_inputs = config.get('native_answer_corrections', [])
    native_requests = {item['attempt_id']: item for item in native_inputs}
    if (len(native_requests) != len(native_inputs)
            or not set(native_requests) <= {row['attempt_id'] for row in records}):
        raise ValueError('Native answer correction has an invalid input scope')
    additional.update(native_requests)
    indexed = {case['id']: case for case in dataset['cases']}
    input_verdicts = None
    if config.get('input_verdicts'):
        proof = json.loads(read_bound(source, config['input_verdicts']).read_text())
        input_verdicts = proof.get('verdicts', {})
        if (proof.get('input_results_sha256') != input_results_sha256
                or set(input_verdicts) != {row['attempt_id'] for row in records}
                or any(type(value) is not bool for value in input_verdicts.values())):
            raise ValueError('Frozen input verdicts do not match the complete input results')
    selected = []
    native_proofs = []
    before_verdicts = {}
    for row in records:
        if row['case_id'] not in replacements and row['attempt_id'] not in additional:
            continue
        case = indexed[row['case_id']]
        raw = contained_path(source, row['artifact_path'], reject_symlinks=True).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['metrics']['content_sha256']:
            raise ValueError('Local revision output integrity failure')
        before = (input_verdicts[row['attempt_id']] if input_verdicts is not None
                  else task_passed(row['ok'], row['metrics']))
        before_verdicts[row['attempt_id']] = before
        if row['attempt_id'] in native_requests:
            from lexbench_webfetch.answer_projection import project_native_answer, project_native_result_fields
            request = native_requests[row['attempt_id']]
            native_path = read_bound(source, request['native_response'])
            metadata = row.get('provider_metadata', {})
            if (request['native_response']['sha256'] != metadata.get('raw_response_sha256')
                    or native_path.stat().st_size != metadata.get('raw_response_bytes')):
                raise ValueError('Native correction source differs from its original response')
            payload = json.loads(native_path.read_bytes())
            answer, fields = project_native_answer(payload, request['projection'])
            if 'result_fields' in request['projection']:
                row.update(project_native_result_fields(payload, request['projection'], request_url=row.get('url')))
            raw = answer.encode('utf-8', errors='replace')
            original_artifact = {'path': row['artifact_path'], 'sha256': row['metrics']['content_sha256']}
            digest = hashlib.sha256(raw).hexdigest()
            old_path = Path(row['artifact_path'])
            new_relative = old_path.with_name(old_path.stem + '-native-' + digest[:12] + old_path.suffix)
            corrected_path = contained_path(source, new_relative, reject_symlinks=True)
            if corrected_path.exists() and corrected_path.read_bytes() != raw:
                raise ValueError('Native correction would overwrite a different artifact')
            corrected_path.parent.mkdir(parents=True, exist_ok=True)
            corrected_path.write_bytes(raw)
            row['artifact_path'] = new_relative.as_posix()
            row['provider_metadata'] = {**metadata, **fields, 'native_answer_projection': request['projection']}
            native_proofs.append({'attempt_id': row['attempt_id'],
                'native_response': request['native_response'], 'projection': request['projection'],
                'original_artifact': original_artifact,
                'corrected_artifact': {'path': row['artifact_path'], 'sha256': digest}})
        row['metrics'] = score_attempt(case, raw, ok=row['ok'], status_code=row.get('status_code'),
            title=row.get('title'), final_url=row.get('final_url'), metadata=row.get('provider_metadata'),
            previous_metrics=row['metrics'], list_reference=load_list_reference(case, source))
        # Diagnoses describe the former score and must be regenerated from this answer.
        row.pop('diagnosis', None)
        row.pop('failure_category', None)
        row['scoring'] = {**row.get('scoring', {}), 'mode': 'offline_rescore',
            'scored_at': datetime.now(timezone.utc).isoformat(),
            'rule_revision': bool(config.get('rule_revision')),
            'input_results_sha256': input_results_sha256}
        selected.append(row['attempt_id'])
    from .local_audit import carry_reviewed_page_identities
    identity_binding = carry_reviewed_page_identities(source, original_dataset, dataset, records)
    changes = [{'attempt_id': row['attempt_id'], 'before': before_verdicts[row['attempt_id']],
                'after': task_passed(row['ok'], row['metrics'])}
               for row in records if row['attempt_id'] in before_verdicts
               and before_verdicts[row['attempt_id']] != task_passed(row['ok'], row['metrics'])]
    apply_cohort_exclusions(source, dataset, records)
    history.mkdir(parents=True, exist_ok=False)
    (history / 'dataset-before.json').write_bytes(dataset_bytes)
    (history / 'results-before.jsonl').write_bytes(results_bytes)
    def binding(path):
        return {'path': str(path.relative_to(source)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    new_dataset = (json.dumps(dataset, ensure_ascii=False, indent=2) + '\n').encode()
    new_results = ''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in records).encode()
    receipt = {'kind': 'local_offline_score_revision', 'provider_calls_added': 0,
        'input_dataset': binding(history / 'dataset-before.json'),
        'input_results': binding(history / 'results-before.jsonl'),
        'output_dataset_sha256': hashlib.sha256(new_dataset).hexdigest(),
        'output_results_sha256': hashlib.sha256(new_results).hexdigest(),
        'scoring_source_sha256': SCORING_SOURCE_SHA256,
        'changed_case_ids': sorted(case['id'] for case in dataset['cases']
            if case != next(c for c in original_dataset['cases'] if c['id'] == case['id'])),
        'rescored_attempt_ids': selected, 'rule_revision': bool(config.get('rule_revision')),
        **({'native_answer_corrections': native_proofs} if native_proofs else {}),
        'input_verdicts': config.get('input_verdicts'),
        **({'predecessor_revision': config['predecessor_revision']} if config.get('predecessor_revision') else {}),
        'superseded_controls': config.get('superseded_controls', []),
        'page_identity_review': identity_binding,
        'task_verdict_changes': changes, 'regression_ids': regressions, 'authority': config['authority']}
    receipt_path = history / 'receipt.json'
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    if (source / 'dataset.json').read_bytes() != dataset_bytes or (source / 'results.jsonl').read_bytes() != results_bytes:
        raise ValueError('Local revision source changed during scoring')
    (source / 'dataset.json').write_bytes(new_dataset)
    (source / 'results.jsonl').write_bytes(new_results)
    try:
        require_local_revision(source, binding(receipt_path), dataset, records)
    except BaseException:
        (source / 'dataset.json').write_bytes(dataset_bytes)
        (source / 'results.jsonl').write_bytes(results_bytes)
        raise
    return receipt
