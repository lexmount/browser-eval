"""Assemble exact headless measurements, never selecting retries by score."""
import argparse
import hashlib
import json
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness.record_io import file_sha256, load_jsonl
from harness.web_fetch.artifacts import (
    copy_cohort_evidence,
    materialize_bound_artifacts,
    materialize_reference_checks,
    materialize_task_references,
)
from lexbench_webfetch.metrics import task_passed
from harness.web_fetch.publication_privacy import project_execution_manifest
from harness.web_fetch.reference_inputs import input_tree, read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    latency_correction_original_elapsed,
    reference_revision_cases,
    require_verified_reference_checks,
    require_certifiable_evidence,
    requires_fresh_reference,
    scoring_revision_history_sha256,
    valid_scoring_revision,
)
from harness.web_fetch.result_review import require_result_review
from lexbench_webfetch.paths import contained_path
from harness.web_fetch.safety import reject_evidence_symlinks, unlinked_path, validate_report_ids
from lexbench_webfetch.scoring_regressions import build_correction_provenance, valid_correction_provenance
from harness.web_fetch.speed_cohort import build_summary as build_speed_summary

REPOSITORY = Path(__file__).resolve().parents[2]

ALLOWED_REVISION_FIELDS = {
    'body_requirements', 'required_patterns', 'required_facts', 'acceptance_contract',
    'forbidden_any', 'expected_resources', 'resource_output_contract', 'task_prompt',
    'reference_answer',
    'mutable_body_policy',
}


def _selected_record(selected, key):
    record = next((row for row, _ in selected
                   if (row.get('case_id'), row.get('provider')) == key), None)
    if record is None:
        raise ValueError('Provider replacement has no prior selected record: '
                         +key[0]+'/'+key[1])
    return record


def read_records(root):
    return load_jsonl(root/'results.jsonl')


def _materialize_canonical_references(output, canonical, canonical_dataset):
    """Copy each assembled case's references from its hash-bound source dataset."""
    declarations = canonical.get('comparison', {}).get('input_sources')
    if not declarations:
        materialize_task_references(output, {name: input_tree(canonical_dataset,name)
            for name in ('sources','list-references')}, [case['id'] for case in canonical['cases']])
        return
    if not isinstance(declarations, list):
        raise ValueError('Canonical input source bindings are invalid')
    expected = {case['id'] for case in canonical['cases']}
    owned = set()
    sources = []
    for declaration in declarations:
        if (not isinstance(declaration, dict)
                or set(declaration) != {'path', 'sha256', 'case_ids'}
                or not isinstance(declaration['case_ids'], list)
                or not declaration['case_ids']):
            raise ValueError('Canonical input source binding is invalid')
        identifiers = declaration['case_ids']
        if (len(identifiers) != len(set(identifiers)) or owned.intersection(identifiers)
                or not set(identifiers) <= expected):
            raise ValueError('Canonical input source ownership is invalid')
        source_dataset = contained_path(REPOSITORY, declaration['path'], reject_symlinks=True)
        raw = read_dataset_bytes(source_dataset)
        if hashlib.sha256(raw).hexdigest() != declaration['sha256']:
            raise ValueError('Canonical input source changed: ' + declaration['path'])
        source_ids = {case['id'] for case in json.loads(raw).get('cases', [])}
        if not set(identifiers) <= source_ids:
            raise ValueError('Canonical input source is missing an owned case')
        trees = {name: input_tree(source_dataset,name)
                 for name in ('sources','list-references')}
        sources.append((set(identifiers), source_ids, trees))
        materialize_task_references(output, trees, identifiers)
        owned.update(identifiers)
    if owned != expected:
        raise ValueError('Canonical input sources do not own the complete case inventory')
    # Some track-specific datasets reuse cases whose source capture remains in
    # an earlier registered dataset.  Fall back only when the owning dataset has
    # no reference for that case; copy every matching fallback so conflicts are
    # still rejected rather than resolved by source order.
    for identifiers, _source_ids, owner_trees in sources:
        for identifier in identifiers:
            owner_has_reference = (
                (owner_trees['sources'] / identifier).exists()
                or (owner_trees['list-references'] / (identifier + '.json')).exists()
            )
            if owner_has_reference:
                continue
            candidates = [trees for _owned, source_ids, trees in sources
                          if identifier in source_ids and (
                              (trees['sources'] / identifier).exists()
                              or (trees['list-references'] / (identifier + '.json')).exists())]
            if not candidates:
                continue
            for trees in candidates:
                materialize_task_references(output, trees, [identifier])


def _sha256_file(path):
    return file_sha256(path)


_PROVIDER_REPLACEMENT_RECEIPT_FIELDS = {
    'schema_version', 'kind', 'provider_calls', 'baseline_dataset_sha256',
    'baseline_results_sha256', 'source_dataset_sha256', 'source_results_sha256',
    'replacements',
}


def _valid_provider_replacement_receipt(receipt):
    if (not isinstance(receipt, dict)
            or set(receipt) != _PROVIDER_REPLACEMENT_RECEIPT_FIELDS
            or receipt.get('schema_version') != 1
            or receipt.get('kind') != 'audited_provider_replacement_scope'
            or receipt.get('provider_calls') != 0):
        return False
    replacements = receipt.get('replacements')
    if not isinstance(replacements, list) or not replacements:
        return False
    seen = set()
    for item in replacements:
        if (not isinstance(item, dict) or set(item) != {
                'provider', 'case_id', 'prior_attempt_id', 'selected_attempt_id',
                'category', 'reason'}
                or item.get('category') not in {
                    'invalid_prior_call', 'input_correction', 'implementation_correction'}
                or not isinstance(item.get('reason'), str) or not item['reason'].strip()):
            return False
        pair = (item.get('provider'), item.get('case_id'))
        if not all(isinstance(value, str) and value for value in (
                *pair, item.get('prior_attempt_id'), item.get('selected_attempt_id'))):
            return False
        if pair in seen:
            return False
        seen.add(pair)
    return True


def _provider_replacement_scope_history(root):
    """Load immutable scope receipts already owned by an assembled baseline."""
    index_path = root/'provider-replacement-scopes.json'
    history_root = root/'history'/'provider-replacement-scopes'
    if not index_path.exists():
        if history_root.exists():
            raise ValueError('Inherited provider replacement scope history has no index')
        return []
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError('Invalid inherited provider replacement scope index')
    try:
        index = json.loads(index_path.read_text())
    except (OSError, ValueError, TypeError) as error:
        raise ValueError('Invalid inherited provider replacement scope index') from error
    if not isinstance(index, list) or not index:
        raise ValueError('Invalid inherited provider replacement scope index')
    checked = []
    seen = set()
    for item in index:
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'replacements'}
                or not isinstance(item.get('path'), str)):
            raise ValueError('Invalid inherited provider replacement scope index')
        relative = Path(item['path'])
        if relative.parent != Path('history/provider-replacement-scopes'):
            raise ValueError('Inherited provider replacement scope is outside its history owner')
        path = contained_path(root, relative, reject_symlinks=True)
        if item['path'] in seen or not path.is_file():
            raise ValueError('Inherited provider replacement scope is missing or duplicated')
        seen.add(item['path'])
        if item.get('sha256') != _sha256_file(path):
            raise ValueError('Inherited provider replacement scope hash differs')
        try:
            receipt = json.loads(path.read_text())
        except (OSError, ValueError, TypeError) as error:
            raise ValueError('Invalid inherited provider replacement scope receipt') from error
        if (not _valid_provider_replacement_receipt(receipt)
                or item.get('replacements') != receipt['replacements']):
            raise ValueError('Invalid inherited provider replacement scope receipt')
        checked.append((item, path, receipt))
    stored = set(history_root.iterdir()) if history_root.is_dir() else set()
    if stored != {path for _, path, _ in checked}:
        raise ValueError('Inherited provider replacement scope history differs from its index')
    return checked


def _selected_provider_replacement_rows(run, root, rows, receipt_path):
    """Select a correction scope from a broader immutable call bundle."""
    if receipt_path is None:
        return rows, None
    receipt_path = unlinked_path(Path(receipt_path))
    if not receipt_path.is_file():
        raise ValueError('Provider replacement scope receipt is missing')
    receipt = json.loads(receipt_path.read_text())
    if (not _valid_provider_replacement_receipt(receipt)
            or receipt.get('baseline_dataset_sha256') != _sha256_file(run/'dataset.json')
            or receipt.get('baseline_results_sha256') != _sha256_file(run/'results.jsonl')
            or receipt.get('source_dataset_sha256') != _sha256_file(root/'dataset.json')
            or receipt.get('source_results_sha256') != _sha256_file(root/'results.jsonl')):
        raise ValueError('Provider replacement scope receipt is not bound to its source evidence')
    replacements = receipt.get('replacements')
    source_by_attempt = {row['attempt_id']: row for row in rows}
    baseline_by_pair = {(row['provider'], row['case_id']): row for row in read_records(run)}
    baseline_cases = {
        case['id']: case for case in json.loads((run/'dataset.json').read_text())['cases']
    }
    selected = []
    seen = set()
    for item in replacements:
        pair = (item['provider'], item['case_id'])
        prior = baseline_by_pair.get(pair)
        current = source_by_attempt.get(item['selected_attempt_id'])
        if (pair in seen or prior is None or current is None
                or prior['attempt_id'] != item['prior_attempt_id']
                or (current['provider'], current['case_id']) != pair):
            raise ValueError('Provider replacement scope differs from bound attempts')
        if (item['category'] == 'invalid_prior_call'
                and prior.get('provider_metadata', {}).get('binary_sha256') !=
                current.get('provider_metadata', {}).get('binary_sha256')):
            raise ValueError('Invalid-call correction changed the provider binary')
        if item['category'] == 'implementation_correction':
            case = baseline_cases.get(item['case_id'])
            if (case is None or (prior.get('ok') is not False and task_passed(
                    True, prior.get('metrics', {}), case.get('task_kind', 'body')))):
                raise ValueError(
                    'Implementation correction baseline machine verdict is not a failure')
        seen.add(pair)
        selected.append(current)
    return selected, receipt


def has_bound_direct_scoring_revision(root):
    """Require a provenance-checked receipt bound to this exact source bundle."""
    path = root/'scoring-revisions.json'
    if not path.is_file():
        return False
    dataset_hash = file_sha256(root/'dataset.json')
    results_hash = file_sha256(root/'results.jsonl')
    try:
        receipts = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return False
    return any(
        isinstance(row, dict)
        and row.get('kind') in {'audited_body_scoring_revision', 'audited_scoring_rule_revision'}
        and row.get('provider_calls') == 0
        and row.get('output_dataset_sha256') == dataset_hash
        and row.get('output_results_sha256') == results_hash
        and valid_correction_provenance(row)
        and all(isinstance(change, dict)
                and set(change.get('fields', ())) <= ALLOWED_REVISION_FIELDS
                for change in row.get('changed_cases', ()))
        for row in receipts if isinstance(receipts, list)
    )


def copy_bound_review(source, output):
    """Carry a prior review only when its immutable inputs remain byte-identical."""
    prior=source/'result-review.json'
    if not prior.is_file():return False
    review=json.loads(prior.read_text())
    bindings=review.get('bindings',{})
    if (bindings.get('dataset_sha256') != file_sha256(output/'dataset.json')
            or bindings.get('results_sha256') != file_sha256(output/'results.jsonl')):
        return False
    shutil.copy2(prior,output/'result-review.json')
    return True


def copy_scoring_revision_history(source, output):
    """Copy scoring receipts together with every hash-bound review archive."""
    receipt_path = source/'scoring-revisions.json'
    if not receipt_path.is_file():
        return False
    receipts = json.loads(receipt_path.read_text())
    if not isinstance(receipts, list):
        raise ValueError('Invalid scoring revision history')
    archives = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError('Invalid scoring revision history')
        relative = receipt.get('prior_result_review_path')
        expected = receipt.get('prior_result_review_sha256')
        if not relative:
            continue
        archived = contained_path(source, relative)
        if not archived.is_file() or archived.is_symlink():
            raise ValueError('Archived result review is missing or unsafe')
        actual = file_sha256(archived)
        if not isinstance(expected, str) or actual != expected:
            raise ValueError('Archived result review hash does not match scoring receipt')
        archives[relative] = archived
    presentation = source / 'report-binding-revisions.json'
    if presentation.is_file():
        for receipt in json.loads(presentation.read_text()):
            for side in ('input', 'output'):
                relative = receipt[f'{side}_dataset_path']
                snapshot = contained_path(source, relative, reject_symlinks=True)
                if file_sha256(snapshot) != receipt[f'{side}_dataset_sha256']:
                    raise ValueError('Presentation revision snapshot hash mismatch')
                archives[relative] = snapshot
        shutil.copy2(presentation, output / presentation.name)
    shutil.copy2(receipt_path, output/'scoring-revisions.json')
    for relative, archived in archives.items():
        target = contained_path(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archived, target)
    return True


def copy_projected_latency_correction(source, output):
    """Bind validated historical timing corrections to unchanged retained attempts."""
    path = source / 'latency-corrections.json'
    if not path.is_file():
        return False
    latency_correction_original_elapsed(source)
    receipt = json.loads(path.read_bytes())
    source_rows = {(row['provider'], row['case_id']): row for row in read_records(source)}
    output_rows = {(row['provider'], row['case_id']): row for row in read_records(output)}
    changes = []
    for change in receipt['changes']:
        key = (change['provider'], change['case_id'])
        prior, current = source_rows[key], output_rows.get(key)
        if current is None or current['attempt_id'] != prior['attempt_id']:
            continue
        if current != prior:
            raise ValueError('Latency projection changed a retained measured attempt')
        changes.append(change)
    if not changes:
        return False
    receipt['changes'] = changes
    receipt['superseded_logical_results'] = len(changes)
    receipt['output_results_sha256'] = file_sha256(output / 'results.jsonl')
    if isinstance(receipt.get('diagnostic_summary'), dict):
        retained = {change['provider'] for change in changes}
        receipt['diagnostic_summary'] = {name: value for name, value in receipt['diagnostic_summary'].items()
                                         if name in retained}
    # The historical selection protocol describes the original measurement.
    # Keep it verbatim and describe this offline projection separately.
    receipt['projection'] = {
        'kind': 'immutable_attempt_subset', 'provider_calls': 0,
        'source_receipt_sha256': file_sha256(path),
        'source_results_sha256': file_sha256(source / 'results.jsonl'),
        'retained_attempt_ids': [output_rows[(change['provider'], change['case_id'])]['attempt_id']
                                 for change in changes],
    }
    target = output / 'latency-corrections.json'
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    latency_correction_original_elapsed(output)
    return True


def copy_projected_review(source, output, excluded_providers):
    """Project a valid review onto a provider-subset without re-judging attempts."""
    prior=source/'result-review.json'
    if not prior.is_file():return False
    source_dataset=json.loads((source/'dataset.json').read_text())
    source_records=read_records(source)
    require_result_review(source,source_dataset,source_records)
    output_dataset=json.loads((output/'dataset.json').read_text())
    output_records=read_records(output)
    if source_dataset.get('cases') != output_dataset.get('cases'):
        raise ValueError('Cannot project a result review across changed cases')
    original = {row['attempt_id']: row for row in source_records}
    if any(row != original.get(row['attempt_id']) for row in output_records):
        raise ValueError('Cannot project a result review across changed attempt evidence')
    review=json.loads(prior.read_text())
    retained={row['attempt_id'] for row in output_records}
    reviewed={row.get('attempt_id') for row in review.get('attempts',[])}
    scope=review.get('review_protocol',{}).get('scope','all')
    if scope == 'all' and not retained <= reviewed:
        raise ValueError('Source result review does not cover the retained provider matrix')
    review['attempts']=[row for row in review.get('attempts',[])
                        if row.get('attempt_id') in retained]
    review['bindings']={
        'dataset_sha256':file_sha256(output/'dataset.json'),
        'results_sha256':file_sha256(output/'results.jsonl'),
    }
    review.setdefault('review_protocol',{})['provider_projection']={
        'excluded_providers':sorted(excluded_providers),
        'provider_calls':0,
        'judgments_reused_only_for_byte_identical_attempt_evidence':True,
    }
    path=output/'result-review.json'
    path.write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
    checked=require_result_review(output,output_dataset,output_records)
    for key in ('expected_attempts','reviewed_attempts','total_attempts',
                'accepted_unreviewed_passes','disagreements','unresolved'):
        review[key]=checked[key]
    path.write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
    return True


def bind_assembled_scoring_revision(output, *, source=None, historical_sources=()):
    """Bind composed scoring; revalidate machine evidence without inventing review."""
    path=output/'scoring-revisions.json'
    review_path=output/'result-review.json'
    if not path.is_file():return False
    if not review_path.is_file():
        if source is None or valid_scoring_revision(output):return False
        if not valid_scoring_revision(source):
            raise ValueError('Assembly source has invalid scoring authority')
        if json.loads((output/'dataset.json').read_text()).get('comparison',{}).get(
                'require_independent_result_review'):
            raise ValueError('Assembly requires its declared independent result review')
        inherited=path.read_bytes()
        if inherited != (source/'scoring-revisions.json').read_bytes():
            raise ValueError('Inherited scoring authority differs from declared source')
        from lexbench_webfetch.scoring_regressions import active_scoring_regressions
        from .scoring_revision import revise
        controls=active_scoring_regressions()
        history=json.loads(inherited)
        identifiers=list(dict.fromkeys(identifier for receipt in history
            for identifier in receipt.get('correction_provenance',{}).get('regression_ids',[])
            if identifier in controls))
        if not identifiers:
            raise ValueError('Assembly scoring authority has no current executable controls')
        categories={controls[identifier]['category'] for identifier in identifiers}
        before=read_records(output)
        archive=output/'diagnostics'/'inherited-scoring-history.json'
        archive.parent.mkdir(parents=True,exist_ok=True)
        archive.write_bytes(inherited)
        path.unlink()
        receipt=revise(output,output/'dataset.json',
            root_cause_category=next(iter(categories)) if len(categories)==1 else 'multi-root',
            root_cause='Revalidate inherited shared scoring corrections against the complete '
                'certified assembled cohort. Task definitions, answers and selected physical '
                'calls remain unchanged; independent result review is not inferred.',
            regression_ids=identifiers,validate_only=True)
        after=read_records(output)
        if receipt['task_decision_changes'] or before!=after:
            raise ValueError('Assembly revalidation changed measured calls or scores')
        receipt['inherited_scoring_authority']={
            'dataset_sha256':file_sha256(source/'dataset.json'),
            'results_sha256':file_sha256(source/'results.jsonl'),
            'receipts_sha256':hashlib.sha256(inherited).hexdigest()}
        path.write_text(json.dumps([receipt],ensure_ascii=False,indent=2)+'\n')
        if not valid_scoring_revision(output):
            raise ValueError('Assembly revalidation left invalid scoring authority')
        return True
    dataset=json.loads((output/'dataset.json').read_text())
    records=read_records(output)
    if historical_sources:
        from .result_review import require_blind_result_review
        require_blind_result_review(output, dataset, records, historical_sources=historical_sources)
    else:
        require_result_review(output, dataset, records)
    if valid_scoring_revision(output):return False
    history=json.loads(path.read_bytes())
    if not isinstance(history,list) or not history:
        raise ValueError('Invalid inherited scoring revision history')
    changed={};decisions={}
    for row in history:
        if (not isinstance(row,dict) or row.get('provider_calls') != 0
                or row.get('kind') not in {'audited_body_scoring_revision',
                                           'audited_scoring_rule_revision',
                                           'audited_assembled_scoring_revision'}):
            raise ValueError('Invalid inherited scoring revision history')
        for change in row.get('changed_cases',[]):
            if set(change.get('fields',()))-ALLOWED_REVISION_FIELDS:
                raise ValueError('Inherited scoring revision changes unsupported fields')
            changed[json.dumps(change,ensure_ascii=False,sort_keys=True)]=change
        for change in row.get('task_decision_changes',[]):
            decisions[json.dumps(change,ensure_ascii=False,sort_keys=True)]=change
    assembled_receipt = {
        'kind':'audited_assembled_scoring_revision',
        'scored_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':history[-1]['output_dataset_sha256'],
        'input_results_sha256':history[-1]['output_results_sha256'],
        'output_dataset_sha256':file_sha256(output/'dataset.json'),
        'output_results_sha256':file_sha256(output/'results.jsonl'),
        'changed_cases':list(changed.values()),
        'task_decision_changes':list(decisions.values()),
        'provider_calls':0,
        'immutable_call_fields':'attempt identity, output bytes/hash, timestamps, elapsed time, metrics and verdicts retained',
        'source_scoring_receipts_sha256':scoring_revision_history_sha256(history),
        'independent_result_review_sha256':file_sha256(review_path),
    }
    scoring_source = history[-1].get('output_scoring_source_sha256')
    if scoring_source is not None:
        assembled_receipt.update(
            input_scoring_source_sha256=scoring_source,
            output_scoring_source_sha256=scoring_source,
        )
    history.append(assembled_receipt)
    candidate=json.dumps(history,ensure_ascii=False,indent=2)+'\n'
    # Validate the complete candidate chain before replacing the real receipt.
    # A failed inherited chain must never be persisted even transiently.
    with tempfile.TemporaryDirectory() as directory:
        check=Path(directory)
        copy_scoring_revision_history(output, check)
        for name in ('dataset.json','results.jsonl','result-review.json','reference-revisions.json'):
            source=output/name
            if source.is_file():shutil.copy2(source,check/name)
        (check/'scoring-revisions.json').write_text(candidate)
        if not valid_scoring_revision(check):
            raise ValueError('Assembled scoring revision would leave an invalid history')
    from harness.reporting.publication import atomic_write
    atomic_write(path, candidate.encode())
    return True


def bind_missing_assembled_scoring_gap(output: Path, assembly_review: Path) -> bool:
    """Insert the one missing assembly edge after a review completed later.

    Pair-only replacement may be assembled before its full blind review exists.
    Later score revisions must not make that provider-replacement transition
    disappear from the hash chain. The supplied historical review must bind
    exactly to the gap target; the current complete review validates the final
    end of the repaired chain.
    """
    path=output/'scoring-revisions.json'
    history=json.loads(path.read_text())
    review=json.loads(assembly_review.read_text())
    if (review.get('review_protocol',{}).get('scope') != 'all'
            or review.get('review_protocol',{}).get('machine_judgments_withheld') is not True
            or review.get('unresolved') != 0
            or review.get('accepted_unreviewed_passes') != 0):
        raise ValueError('Assembly review must be a complete resolved blind review')
    gaps=[]
    for index in range(1,len(history)):
        previous=(history[index-1].get('output_dataset_sha256'),
                  history[index-1].get('output_results_sha256'))
        current=(history[index].get('input_dataset_sha256'),
                 history[index].get('input_results_sha256'))
        if previous != current:
            gaps.append((index,previous,current))
    if not gaps:
        return False
    if len(gaps) != 1:
        raise ValueError('Scoring history has more than one missing assembly edge')
    index,before,after=gaps[0]
    if review.get('bindings') != {
            'dataset_sha256':after[0],'results_sha256':after[1]}:
        raise ValueError('Assembly review is not bound to the missing edge target')
    changed={};decisions={}
    for row in history[:index]:
        for change in row.get('changed_cases',[]):
            changed[json.dumps(change,ensure_ascii=False,sort_keys=True)]=change
        for decision in row.get('task_decision_changes',[]):
            decisions[json.dumps(decision,ensure_ascii=False,sort_keys=True)]=decision
    bridge={
        'kind':'audited_assembled_scoring_revision',
        'scored_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':before[0],
        'input_results_sha256':before[1],
        'output_dataset_sha256':after[0],
        'output_results_sha256':after[1],
        'changed_cases':list(changed.values()),
        'task_decision_changes':list(decisions.values()),
        'provider_calls':0,
        'immutable_call_fields':'attempt identity, output bytes/hash, timestamps, elapsed time, metrics and verdicts retained',
        'source_scoring_receipts_sha256':scoring_revision_history_sha256(history[:index]),
        'independent_result_review_sha256':file_sha256(assembly_review),
    }
    repaired=[*history[:index],bridge,*history[index:]]
    candidate=json.dumps(repaired,ensure_ascii=False,indent=2)+'\n'
    with tempfile.TemporaryDirectory() as directory:
        check=Path(directory)
        copy_scoring_revision_history(output, check)
        for name in ('dataset.json','results.jsonl','result-review.json','reference-revisions.json'):
            source=output/name
            if source.is_file():shutil.copy2(source,check/name)
        (check/'scoring-revisions.json').write_text(candidate)
        if not valid_scoring_revision(check):
            raise ValueError('Repaired assembly scoring history is still invalid')
    path.write_text(candidate)
    return True


def read_verified_batches(root, dataset, rows):
    """Accept either one physical run or a previously verified assembly."""
    manifest_path=root/'manifest.json'
    execution_path=root/'execution.json'
    if execution_path.exists():
        batches=json.loads(execution_path.read_text())['batches']
    elif manifest_path.exists():
        batches=[json.loads(manifest_path.read_text())]
    else:
        raise ValueError('Evidence has neither manifest.json nor execution.json: '+str(root))
    require_verified_reference_checks(batches,cases=dataset,records=rows,source=root,
        equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
        'equivalent_reference_bytes_with_independent_batch_captures')
    return batches


def _without_reference(case):
    value=deepcopy(case)
    value.pop('source_verification',None);value.pop('ground_truth_main_content',None)
    return value


def _provider_request_contract(case, provider):
    """Return the task fields that can affect this provider's physical call.

    Firecrawl records ``minimum_text_fragments`` as unsupported and never puts
    it in the Scrape request.  A cohort frozen immediately before that
    evaluator-only completion diagnostic was added is therefore physically
    equivalent for Firecrawl, while request-bearing task fields remain exact.
    """
    value = _without_reference(case)
    # Reader-facing explanation and independent reference provenance are not
    # sent to providers.  Keep the physical-call comparison focused on the
    # URL, requested scope and execution/acceptance contract.
    value.pop("description", None)
    value.pop("ground_truth_evidence", None)
    benchmark = value.get("source_benchmark", {})
    annotation = benchmark.get("stable_annotation", {})
    if annotation.get("content_kind") == "changing_public_body":
        # An early dataset migration stored acceptance/report metadata beside
        # source-capture metadata.  It never entered any provider request.
        # Normalize that draft representation so certified calls can migrate
        # to the explicit mutable_body_policy field without being rerun.
        benchmark.pop("snapshot_metric_scope", None)
        benchmark.pop("stable_annotation", None)
    if provider == "firecrawl":
        completion = value.get("retrieval_hints", {}).get("completion", {})
        completion.pop("minimum_text_fragments", None)
    return value


def _prune_shadowed_batch_attempts(batches, records, original_latency=None):
    """Keep an attempt ID only in the batch whose time bounds contain the selected call.

    Attempt IDs are stable provider/case identities and can recur in a deliberate
    implementation rerun. Time bounds disambiguate the selected physical call.
    """
    by_id={r['attempt_id']:r for r in records}
    original_latency = original_latency or {}
    result=[]
    for batch in batches:
        start=datetime.fromisoformat(batch['started_at'])
        end=datetime.fromisoformat(batch['completed_at'])+timedelta(milliseconds=.01)
        kept=[]
        for attempt_id in batch.get('attempt_ids',[]):
            record=by_id.get(attempt_id)
            if not record:
                continue
            began=datetime.fromisoformat(record['started_at'])
            elapsed = original_latency.get((record.get('provider'), record['case_id']), record['elapsed_ms'])
            finished=max(began+timedelta(milliseconds=elapsed),
                datetime.fromisoformat(record.get('completed_at',record['started_at'])))
            if start<=began<=finished<=end:
                kept.append(attempt_id)
        if not kept:
            continue
        batch={**batch,'attempt_ids':kept,'attempt_count':len(kept)}
        if isinstance(batch.get('providers'),list):
            retained_providers={by_id[attempt_id]['provider'] for attempt_id in kept}
            batch['providers']=[row for row in batch['providers']
                                if isinstance(row,dict) and row.get('name') in retained_providers]
        retained_cases={by_id[attempt_id]['case_id'] for attempt_id in kept}
        checks=batch.get('reference_checks')
        if checks:
            checks=deepcopy(checks)
            checks['cases']={cid:row for cid,row in checks.get('cases',{}).items()
                             if cid in retained_cases}
            if checks.get('postcheck'):
                checks['postcheck']['cases']={cid:row for cid,row in checks['postcheck'].get('cases',{}).items()
                                              if cid in retained_cases}
            if checks['cases']:
                batch['reference_checks']=checks
            else:
                batch.pop('reference_checks',None)
        batch.setdefault('projection',{})['shadowed_attempt_ids_pruned']=True
        result.append(batch)
    return result


def _project_provider_subset(dataset, records, batches, audit, excluded_providers, original_latency=None):
    """Return a complete publication matrix with explicitly excluded providers removed."""
    excluded=set(excluded_providers)
    if not excluded:
        return dataset,records,batches,audit
    if any(not isinstance(name,str) or not name for name in excluded):
        raise ValueError('Excluded provider names must be non-empty strings')
    coverage=dataset.get('coverage_plan',{})
    declared={name for providers in coverage.values() for name in providers}
    unknown=excluded-declared
    if unknown:
        raise ValueError('Cannot exclude providers absent from the declared matrix: '+', '.join(sorted(unknown)))
    projected=deepcopy(dataset)
    active_tracks={case.get('track','web') for case in projected.get('cases',[])}
    for track,providers in projected.get('coverage_plan',{}).items():
        kept=[name for name in providers if name not in excluded]
        if track in active_tracks and not kept:
            raise ValueError('Provider exclusion would leave an active track empty: '+track)
        projected['coverage_plan'][track]=kept
    kept_records=[row for row in records if row['provider'] not in excluded]
    expected={(case['id'],provider) for case in projected.get('cases',[])
              for provider in projected['coverage_plan'][case.get('track','web')]}
    actual={(row['case_id'],row['provider']) for row in kept_records}
    if len(kept_records)!=len(expected) or actual!=expected:
        raise ValueError('Provider exclusion did not preserve a complete case/provider matrix')
    known_providers=sorted(declared,key=len,reverse=True)
    def attempt_provider(attempt_id):
        return next((name for name in known_providers
                     if isinstance(attempt_id,str) and attempt_id.startswith(name+'__')),None)
    projected_audit=[]
    for row in audit:
        if row.get('provider') in excluded:
            continue
        value=deepcopy(row)
        if isinstance(value.get('excluded_attempts'),list):
            value['excluded_attempts']=[attempt for attempt in value['excluded_attempts']
                if attempt_provider(attempt.get('attempt_id')) not in excluded]
        projected_audit.append(value)
    projected_batches=_prune_shadowed_batch_attempts(batches,kept_records,original_latency)
    projected.setdefault('comparison',{})['excluded_providers']=sorted(excluded)
    projected['comparison']['provider_projection']=(
        'Explicit publication projection only; provider adapters and immutable source calls are retained outside this bundle.')
    return projected,kept_records,projected_batches,projected_audit


def _write_projected_reference_revisions(source, target, case_ids):
    """Rebind validated reference corrections to an exact provider projection."""
    covered=reference_revision_cases(source)
    path=source/'reference-revisions.json'
    if not covered or not path.is_file():
        return False
    receipts=json.loads(path.read_text())
    changes=[];transitions=[];provenance=[]
    for receipt in receipts if isinstance(receipts,list) else ():
        if (not isinstance(receipt,dict)
                or receipt.get('kind') != 'audited_reference_correction'
                or receipt.get('provider_calls') != 0
                or not valid_correction_provenance(receipt)):
            continue
        selected=[deepcopy(change) for change in receipt.get('changed_cases',())
                  if isinstance(change,dict)
                  and change.get('case_id') in case_ids
                  and change.get('old_reference_sha256') in
                      covered.get(change.get('case_id'),set())]
        if not selected:
            continue
        changes.extend(selected)
        provenance.append(receipt['correction_provenance'])
        if isinstance(receipt.get('revision_transitions'),list):
            transitions.extend(deepcopy(receipt['revision_transitions']))
    if not changes:
        return False
    unique={(row['case_id'],row['old_reference_sha256'],row['new_reference_sha256']):row
            for row in changes}
    categories={row['category'] for row in provenance}
    regression_ids=list(dict.fromkeys(identifier for row in provenance
                                       for identifier in row['regression_ids']))
    root_causes=list(dict.fromkeys(row['root_cause'] for row in provenance))
    receipt={
        'kind':'audited_reference_correction',
        'corrected_at':datetime.now(timezone.utc).isoformat(),
        'output_dataset_sha256':file_sha256(target/'dataset.json'),
        'output_results_sha256':file_sha256(target/'results.jsonl'),
        'changed_cases':list(unique.values()),
        'task_decision_changes':[],
        'provider_calls':0,
        'immutable_call_fields':'attempt identity, output bytes/hash, timestamps and elapsed time retained',
        'source_receipts_sha256':[file_sha256(path)],
        'revision_transitions':list({json.dumps(row,sort_keys=True):row
                                     for row in transitions}.values()),
    }
    receipt['correction_provenance']=build_correction_provenance(
        next(iter(categories)) if len(categories)==1 else 'multi-root',
        'Projected validated reference corrections: '+'; '.join(root_causes),
        regression_ids)
    (target/'reference-revisions.json').write_text(
        json.dumps([receipt],ensure_ascii=False,indent=2)+'\n')
    return True


def _copy_speed_bundle(source, target, dataset, rows, batches, excluded_providers=()):
    """Copy only explicitly bound speed evidence into an aggregate tree."""
    speed_declared={name for providers in dataset.get('coverage_plan',{}).values()
                    for name in providers}
    excluded_providers=sorted(set(excluded_providers)&speed_declared)
    source_execution=json.loads((source/'execution.json').read_text())
    source_aggregate=source_execution.get('aggregate')
    projected_dataset,projected_rows,projected_batches=dataset,rows,batches
    original_latency = latency_correction_original_elapsed(source)
    if excluded_providers:
        source_summary=json.loads((source/'speed-comparison.json').read_text())
        rebuilt=build_speed_summary(dataset,rows,batches,source_aggregate)
        if ({key:value for key,value in source_summary.items() if key!='bindings'} != rebuilt
                or source_summary.get('bindings') != {
                    'dataset_sha256':file_sha256(source/'dataset.json'),
                    'results_sha256':file_sha256(source/'results.jsonl')}):
            raise ValueError('Speed comparison is not bound to its complete source cohort')
        projected_dataset,projected_rows,projected_batches,_=_project_provider_subset(
            deepcopy(dataset),rows,batches,[],excluded_providers,original_latency)

    copy_cohort_evidence(source, target, projected_dataset, projected_rows, projected_batches,
                         summary_name='speed-comparison.json', include_private=True,
                         include_metadata=not excluded_providers)
    if excluded_providers:
        aggregate=deepcopy(source_aggregate)
        aggregate['providers']=[name for name in aggregate['providers']
                                if name not in set(excluded_providers)]
        aggregate['batch_count']=len(projected_batches)
        execution={**source_execution,'batches':projected_batches,'aggregate':aggregate}
        (target/'dataset.json').write_text(json.dumps(projected_dataset,ensure_ascii=False,indent=2)+'\n')
        (target/'results.jsonl').write_text(''.join(
            json.dumps(row,ensure_ascii=True)+'\n' for row in projected_rows))
        (target/'execution.json').write_text(json.dumps(execution,ensure_ascii=False,indent=2)+'\n')
        summary=build_speed_summary(projected_dataset,projected_rows,projected_batches,aggregate)
        summary['bindings']={
            'dataset_sha256':file_sha256(target/'dataset.json'),
            'results_sha256':file_sha256(target/'results.jsonl'),
        }
        (target/'speed-comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        copy_projected_review(source,target,excluded_providers)
        _write_projected_reference_revisions(
            source,target,{case['id'] for case in projected_dataset.get('cases',[])})
        copy_projected_latency_correction(source,target)
        require_verified_reference_checks(projected_batches,cases=projected_dataset,
            records=projected_rows,source=target,allow_scoring_rule_revision=True)


def _assemble(run, certificates, replacements, output, additions=None, provider_replacements=None,
              provider_additions=None, speed_cohort=None, resource_cohort=None,
              canonical_dataset=None, retain_unverified=False, exclude_providers=None,
              provider_replacement_scope_receipts=None):
    additions = additions or []
    provider_replacements = provider_replacements or []
    provider_replacement_scope_receipts = provider_replacement_scope_receipts or []
    if provider_replacement_scope_receipts and (
            len(provider_replacement_scope_receipts) != len(provider_replacements)):
        raise ValueError('Provider replacement sources and scope receipts differ in count')
    provider_additions = provider_additions or []
    exclude_providers = exclude_providers or []
    if output.exists():raise ValueError('Destination exists; evidence must not be overwritten')
    dataset=json.loads((run/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases']}
    reference_input_roots={identifier:run for identifier in cases}
    canonical=(json.loads(read_dataset_bytes(canonical_dataset)) if canonical_dataset else None)
    canonical_cases={c['id']:c for c in canonical['cases']} if canonical else {}
    if canonical:
        canonical_comparison = canonical.get('comparison', {})
        comparison = dataset.setdefault('comparison', {})
        for key in ('require_independent_result_review',
                    'independent_result_review_scope'):
            if key in canonical_comparison:
                comparison[key] = deepcopy(canonical_comparison[key])
    case_replacements=(canonical.get('comparison',{}).get('case_replacements',{})
                       if canonical else {})
    if (not isinstance(case_replacements,dict)
            or len(set(case_replacements.values())) != len(case_replacements)):
        raise ValueError('Canonical case replacement map is invalid')
    replacement_targets={new:old for old,new in case_replacements.items()}
    original=read_records(run)
    expected={(c['id'],p) for c in cases.values() for p in dataset['coverage_plan'][c.get('track','web')]}
    if len(original)!=len(expected) or {(r['case_id'],r['provider']) for r in original}!=expected:
        raise ValueError('Original run has incomplete coverage')
    source_manifest=(json.loads((run/'manifest.json').read_text())
                     if (run/'manifest.json').exists() else {})
    require_certifiable_evidence(dataset, [source_manifest])
    live_physical_run=(source_manifest.get('reference_checks',{}).get('status') ==
                       'awaiting_postcheck'
                       and source_manifest.get('attempt_count') == len(original)
                       and set(source_manifest.get('attempt_ids',[])) ==
                       {row['attempt_id'] for row in original})
    assembled_input=(run/'execution.json').exists() and not live_physical_run
    inherited_replacement_scope_history = (
        _provider_replacement_scope_history(run) if assembled_input else []
    )
    replacement_cases={};batches=[];evidence=[];selected=[];audit=[]
    reference_evidence=[]
    if assembled_input:
        prior_batches=json.loads((run/'execution.json').read_text())['batches']
        require_verified_reference_checks(prior_batches,cases=dataset,records=original,source=run,
            equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
            'equivalent_reference_bytes_with_independent_batch_captures',
            allow_scoring_rule_revision=valid_scoring_revision(run))
        batches.extend(project_execution_manifest(batch) for batch in prior_batches)
        evidence.append(run)
        reference_evidence.append((run, prior_batches))
        prior_audit=run/'selection-audit.json'
        if prior_audit.is_file():
            inherited=json.loads(prior_audit.read_text())
            if not isinstance(inherited,list):
                raise ValueError('Invalid inherited selection audit')
            audit.extend(inherited)
    for root in replacements:
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        replacement_batches=read_verified_batches(root,new_dataset,rows)
        for case in new_dataset['cases']:
            cid=case['id']
            target_id=replacement_targets.get(cid,cid)
            if target_id not in cases or target_id in replacement_cases:raise ValueError('Unknown/duplicate replacement case')
            before,after=_without_reference(cases[target_id]),_without_reference(case)
            if canonical:
                for key in ('id','url','track','task_kind'):
                    if case.get(key)!=canonical_cases[cid].get(key):
                        raise ValueError(f'Replacement changed canonical {key}: '+cid)
            elif before!=after:
                raise ValueError('Replacement changed task rules without a canonical dataset: '+cid)
            replacement_cases[target_id]=(case,root)
            reference_input_roots[cid]=root
            old_providers=dataset['coverage_plan'][cases[target_id].get('track','web')]
            new_providers=new_dataset['coverage_plan'][case.get('track','web')]
            if set(old_providers)!=set(new_providers):
                raise ValueError('Replacement provider scope changed: '+cid)
            expected-={(target_id,p) for p in old_providers}
            expected|={(cid,p) for p in new_providers}
            audit.append({'case_id':cid,'replaces_case_id':target_id,'selection_reason':('canonical task/reference correction with a complete matched cohort; no score-based selection'
                if before!=after else 'fresh complete matched cohort after failed reference certificate; no score-based selection'),
                'excluded_attempts':[{k:r[k] for k in ('attempt_id','run_id','started_at','elapsed_ms')} for r in original if r['case_id']==target_id]})
        batches.extend(project_execution_manifest(batch) for batch in replacement_batches);evidence.append(root)
        reference_evidence.append((root, replacement_batches))
        selected.extend((r,root) for r in rows)
    for cid,case in cases.items():
        if cid in replacement_cases:continue
        rows=[r for r in original if r['case_id']==cid]
        if requires_fresh_reference(canonical_cases.get(cid,case)) and not assembled_input:
            if certificates is None:
                raise ValueError('Fresh-reference assembly requires certificate evidence: '+cid)
            cert=certificates/cid
            manifest=json.loads((cert/'manifest.json').read_text())
            if {r['attempt_id']:r for r in read_records(cert)}!={r['attempt_id']:r for r in rows}:
                raise ValueError('Certificate records differ from original call records: '+cid)
            batches.append(project_execution_manifest(manifest));evidence.append(cert)
            reference_evidence.append((cert, [manifest]))
        selected.extend((r,run) for r in rows)
    stable=[r for r in original if not requires_fresh_reference(canonical_cases.get(r['case_id'],cases[r['case_id']])) and r['case_id'] not in replacement_cases]
    if stable and not assembled_input:
        manifest=json.loads((run/'manifest.json').read_text())
        manifest.pop('reference_checks',None)  # only non-snapshot cases in this projection
        manifest.update(attempt_count=len(stable),attempt_ids=[r['attempt_id'] for r in stable])
        manifest['dataset']['case_count']=len({r['case_id'] for r in stable})
        manifest['projection']={'kind':'non-snapshot-cases-only','source_manifest_sha256':file_sha256(run/'manifest.json')}
        batches.append(project_execution_manifest(manifest))
    for root in additions:
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        addition_batches=read_verified_batches(root,new_dataset,rows)
        incoming={c['id']:c for c in new_dataset['cases']}
        if set(incoming)&set(cases):raise ValueError('Addition overlaps existing cases')
        planned={(c['id'],p) for c in incoming.values() for p in new_dataset['coverage_plan'][c.get('track','web')]}
        if {(r['case_id'],r['provider']) for r in rows}!=planned:raise ValueError('Addition has incomplete coverage')
        for cid,case in incoming.items():
            if canonical and _without_reference(case)!=_without_reference(canonical_cases[cid]):
                raise ValueError('Addition differs from canonical task rules: '+cid)
            cases[cid]=case
            reference_input_roots[cid]=root
            dataset['coverage_plan'].setdefault(case.get('track','web'),new_dataset['coverage_plan'][case.get('track','web')])
        expected|=planned
        selected.extend((r,root) for r in rows)
        batches.extend(project_execution_manifest(batch) for batch in addition_batches);evidence.append(root)
        reference_evidence.append((root, addition_batches))
        audit.extend({'case_id':cid,'selection_reason':'missing case completed as the full declared provider cohort; no score-based selection','excluded_attempts':[]} for cid in incoming)
    pair_replacements = {}
    replacement_scope_receipts = []
    for replacement_index, root in enumerate(provider_replacements):
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        replacement_batches=read_verified_batches(root,new_dataset,rows)
        receipt_path = (provider_replacement_scope_receipts[replacement_index]
                        if provider_replacement_scope_receipts else None)
        rows, scope_receipt = _selected_provider_replacement_rows(
            run, root, rows, receipt_path)
        if scope_receipt is not None:
            replacement_scope_receipts.append((Path(receipt_path), scope_receipt))
        scope_by_pair = {
            (item['case_id'], item['provider']): item
            for item in (scope_receipt or {}).get('replacements', [])
        }
        incoming={c['id']:c for c in new_dataset['cases']}
        for row in rows:
            key=(row['case_id'],row['provider'])
            if key not in expected or key in pair_replacements:
                raise ValueError('Unknown/duplicate provider replacement: '+row['case_id']+'/'+row['provider'])
            case=incoming[row['case_id']]
            comparison=canonical_cases.get(row['case_id'],cases[row['case_id']])
            if _provider_request_contract(case, row['provider']) != \
                    _provider_request_contract(comparison, row['provider']):
                raise ValueError('Provider replacement changed task rules: '+row['case_id'])
            if requires_fresh_reference(comparison):
                if case.get('ground_truth_main_content')!=comparison.get('ground_truth_main_content'):
                    raise ValueError('Provider replacement used different reference bytes: '+row['case_id'])
                if (case.get('source_verification',{}).get('reference_sha256') !=
                        comparison.get('source_verification',{}).get('reference_sha256')):
                    raise ValueError('Provider replacement reference hash differs: '+row['case_id'])
            pair_replacements[key]=(row,root)
            old=_selected_record(selected,key)
            declared = scope_by_pair.get(key)
            audit.append({'case_id':row['case_id'],'provider':row['provider'],
                'selection_reason':(
                    declared['reason'] if declared else
                    'predeclared provider implementation replacement; complete scope selected without score-based retries'),
                **({'selection_category': declared['category']}
                   if declared else {}),
                'excluded_attempts':[{k:old[k] for k in ('attempt_id','run_id','started_at','elapsed_ms')}]})
        batches.extend(project_execution_manifest(batch) for batch in replacement_batches);evidence.append(root)
        reference_evidence.append((root, replacement_batches))
    added_providers=set()
    for root in provider_additions:
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        addition_batches=read_verified_batches(root,new_dataset,rows)
        incoming={c['id']:c for c in new_dataset['cases']}
        if set(incoming)!=set(cases):
            raise ValueError('Provider addition must cover every canonical case')
        providers={r['provider'] for r in rows}
        existing={r['provider'] for r in original}
        if not providers or providers & existing or providers & added_providers:
            raise ValueError('Provider addition must contain only new provider names')
        planned={(cid,provider) for cid in cases for provider in providers}
        if {(r['case_id'],r['provider']) for r in rows}!=planned:
            raise ValueError('Provider addition has incomplete case coverage')
        for cid,case in incoming.items():
            comparison=canonical_cases.get(cid,cases[cid])
            if _without_reference(case)!=_without_reference(comparison):
                raise ValueError('Provider addition changed task rules: '+cid)
            if requires_fresh_reference(comparison):
                if case.get('ground_truth_main_content')!=comparison.get('ground_truth_main_content'):
                    raise ValueError('Provider addition used different reference bytes: '+cid)
        expected|=planned
        added_providers|=providers
        selected.extend((r,root) for r in rows)
        batches.extend(project_execution_manifest(batch) for batch in addition_batches);evidence.append(root)
        reference_evidence.append((root, addition_batches))
        audit.extend({'provider':provider,
                      'selection_reason':'predeclared full-case provider addition; no score-based retries',
                      'excluded_attempts':[]} for provider in sorted(providers))
    dataset['cases']=[replacement_cases.get(cid,(case,None))[0] for cid,case in cases.items()]
    if canonical:
        resulting_ids={replacement_cases.get(cid,(case,None))[0]['id'] for cid,case in cases.items()}
        if set(canonical_cases)!=resulting_ids:raise ValueError('Assembled case inventory differs from canonical dataset')
        dataset['cases']=[deepcopy(case) for case in canonical['cases']]
    if pair_replacements:
        selected=[pair_replacements.get((r['case_id'],r['provider']),(r,root)) for r,root in selected]
    records=[r for r,_ in selected]
    # Reassembly must not invalidate an exact result review merely by changing
    # JSONL row order. Preserve the source cohort's order; genuinely added
    # attempts retain their existing relative order after the source rows.
    source_order={row['attempt_id']:index for index,row in enumerate(original)}
    records.sort(key=lambda row: source_order.get(row['attempt_id'], len(source_order)))
    timings_by_root = {root: latency_correction_original_elapsed(root) for root in {root for _, root in selected}}
    original_latency = {(row['provider'], row['case_id']): timings_by_root[root][(row['provider'], row['case_id'])]
                        for row, root in selected if (row['provider'], row['case_id']) in timings_by_root[root]}
    batches=_prune_shadowed_batch_attempts(batches,records,original_latency)
    if provider_replacements:
        dataset['comparison']['reference_binding_mode']='equivalent_reference_bytes_with_independent_batch_captures'
    for track in dataset.get('coverage_plan',{}):
        dataset['coverage_plan'][track]=list(dict.fromkeys(dataset['coverage_plan'][track]+sorted(added_providers)))
    dataset,records,batches,audit=_project_provider_subset(
        dataset,records,batches,audit,exclude_providers,original_latency)
    retained_attempt_ids={row['attempt_id'] for row in records}
    selected=[(row,root) for row,root in selected
              if row['attempt_id'] in retained_attempt_ids]
    expected={(case['id'],provider) for case in dataset['cases']
              for provider in dataset['coverage_plan'][case.get('track','web')]}
    validate_report_ids(dataset,records)
    if len(records)!=len(expected) or {(r['case_id'],r['provider']) for r in records}!=expected:
        raise ValueError('Assembled coverage differs from the complete cohort')
    failed=[cid for batch in batches for cid,row in batch.get('reference_checks',{}).get('postcheck',{}).get('cases',{}).items() if row['status']!='verified']
    dataset['comparison']['publication_state']='blocked_reference_checks' if failed else 'verified'
    dataset['comparison']['unverified_reference_cases']=sorted(set(failed))
    has_reference_revisions=any(reference_revision_cases(root) for root in evidence)
    if not retain_unverified and not has_reference_revisions:
        # Certificate chronology uses the content-producing call's validated
        # timing. Persisted rows retain their corrected published timing.
        physical_records = [{**row, 'elapsed_ms': original_latency.get(
            (row['provider'], row['case_id']), row['elapsed_ms'])} for row in records]
        require_verified_reference_checks(batches,cases=dataset,records=physical_records,
            equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
            'equivalent_reference_bytes_with_independent_batch_captures',
            allow_scoring_rule_revision=valid_scoring_revision(run))
    output.mkdir(parents=True)
    reference_groups={}
    for case in dataset['cases']:
        path=canonical_dataset or reference_input_roots[case['id']]/'dataset.json'
        reference_groups.setdefault(path,[]).append(case['id'])
    if canonical:
        _materialize_canonical_references(output, canonical, canonical_dataset)
    else:
        for path, identifiers in reference_groups.items():
            materialize_task_references(output, {name: input_tree(path,name)
                for name in ('sources','list-references')}, identifiers)
    for row,root in selected:
        materialize_bound_artifacts(output, root, [row])
    for root, source_batches in reference_evidence:
        materialize_reference_checks(output, root, source_batches)
    # Keep rejected calls, scores and certificates available outside rankings.
    for cid in replacement_cases:
        target=output/'diagnostics'/'replaced-cases'/cid;target.mkdir(parents=True)
        rows=[r for r in original if r['case_id']==cid]
        # Keep the existing diagnostic serialization stable for downstream tools.
        (target/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=True)+'\n' for r in rows))
        certificate=certificates/cid if certificates is not None else None
        if certificate is not None and certificate.exists():
            rejected=project_execution_manifest(json.loads((certificate/'manifest.json').read_text()))
            (target/'manifest.json').write_text(json.dumps(rejected,ensure_ascii=False,indent=2)+'\n')
        elif (run/'manifest.json').exists():
            rejected=project_execution_manifest(json.loads((run/'manifest.json').read_text()))
            rejected.pop('reference_checks',None)
            rejected.update(attempt_count=len(rows),attempt_ids=[r['attempt_id'] for r in rows])
            rejected['projection']={'kind':'replaced-non-snapshot-case','source_manifest_sha256':file_sha256(run/'manifest.json')}
            (target/'manifest.json').write_text(json.dumps(rejected,ensure_ascii=False,indent=2)+'\n')
        else:
            rejected_batches=_prune_shadowed_batch_attempts(
                [project_execution_manifest(batch) for batch in prior_batches],rows,
                latency_correction_original_elapsed(run))
            if not rejected_batches:
                raise ValueError('Assembled input has no execution batch for replaced case: '+cid)
            (target/'execution.json').write_text(json.dumps(
                {'batches':rejected_batches},ensure_ascii=False,indent=2)+'\n')
        if certificate is not None and (certificate/'manifest.json').is_file():
            materialize_reference_checks(target, certificate,
                [json.loads((certificate/'manifest.json').read_text())])
        materialize_bound_artifacts(target, run, rows)
    for name,payload in [('dataset.json',dataset),('execution.json',{'batches':batches}),('selection-audit.json',audit)]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    (output/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=True)+'\n' for r in records))
    if inherited_replacement_scope_history or replacement_scope_receipts:
        index = []
        for item, source, _ in inherited_replacement_scope_history:
            target = contained_path(output, item['path'], reject_symlinks=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            index.append(item)
        history = output/'history'/'provider-replacement-scopes'
        history.mkdir(parents=True, exist_ok=True)
        for position, (receipt_path, receipt) in enumerate(replacement_scope_receipts):
            target = history/f'{len(index) + position:03d}-{receipt_path.name}'
            shutil.copy2(receipt_path, target)
            index.append({
                'path': str(target.relative_to(output)),
                'sha256': _sha256_file(target),
                'replacements': receipt['replacements'],
            })
        (output/'provider-replacement-scopes.json').write_text(
            json.dumps(index, ensure_ascii=False, indent=2)+'\n')
    copy_scoring_revision_history(run, output)
    copy_projected_latency_correction(run, output)
    # Consolidate independently validated source reference-correction receipts
    # and bind them to the exact assembled dataset/results pair.
    revision_rows=[]
    revision_transitions=[]
    revision_provenance=[]
    for root in dict.fromkeys(evidence):
        covered=reference_revision_cases(root)
        if not covered:continue
        receipts=json.loads((root/'reference-revisions.json').read_text())
        for receipt in receipts:
            if (not isinstance(receipt,dict)
                    or receipt.get('kind') != 'audited_reference_correction'
                    or receipt.get('provider_calls') != 0
                    or not valid_correction_provenance(receipt)):
                continue
            reachable=[]
            for change in receipt.get('changed_cases',[]):
                if (isinstance(change,dict)
                        and change.get('old_reference_sha256') in
                        covered.get(change.get('case_id'),set())):
                    reachable.append(deepcopy(change))
            if not reachable:
                continue
            revision_rows.extend(reachable)
            revision_provenance.append(deepcopy(receipt['correction_provenance']))
            transitions=receipt.get('revision_transitions')
            if isinstance(transitions,list):
                revision_transitions.extend(deepcopy(transitions))
            elif all(isinstance(receipt.get(key),str) for key in (
                    'input_dataset_sha256','input_results_sha256')):
                revision_transitions.append({
                    'input_dataset_sha256':receipt['input_dataset_sha256'],
                    'input_results_sha256':receipt['input_results_sha256'],
                    'output_dataset_sha256':receipt.get(
                        'prior_output_dataset_sha256',receipt['output_dataset_sha256']),
                    'output_results_sha256':receipt.get(
                        'prior_output_results_sha256',receipt['output_results_sha256']),
                })
    if revision_rows:
        unique={(row['case_id'],row['old_reference_sha256'],row['new_reference_sha256']):row
                for row in revision_rows}
        consolidated={
            'kind':'audited_reference_correction',
            'corrected_at':datetime.now(timezone.utc).isoformat(),
            'output_dataset_sha256':file_sha256(output/'dataset.json'),
            'output_results_sha256':file_sha256(output/'results.jsonl'),
            'changed_cases':list(unique.values()),
            'task_decision_changes':[],
            'provider_calls':0,
            'immutable_call_fields':'attempt identity, output bytes/hash, timestamps and elapsed time retained',
            'source_receipts_sha256':sorted({file_sha256(root/'reference-revisions.json')
                                             for root in dict.fromkeys(evidence)
                                             if (root/'reference-revisions.json').is_file()}),
            'revision_transitions':list({json.dumps(row,sort_keys=True):row
                                         for row in revision_transitions}.values()),
        }
        categories={row['category'] for row in revision_provenance}
        regression_ids=list(dict.fromkeys(identifier for row in revision_provenance
                                           for identifier in row['regression_ids']))
        root_causes=list(dict.fromkeys(row['root_cause'] for row in revision_provenance))
        consolidated['correction_provenance']=build_correction_provenance(
            next(iter(categories)) if len(categories)==1 else 'multi-root',
            'Consolidated validated reference corrections: '+'; '.join(root_causes),
            regression_ids)
        (output/'reference-revisions.json').write_text(json.dumps([consolidated],ensure_ascii=False,indent=2)+'\n')
    if exclude_providers:
        review_copied=copy_projected_review(run,output,exclude_providers)
        if (dataset.get('comparison',{}).get('require_independent_result_review')
                and not review_copied):
            raise ValueError('Provider projection requires a projectable independent result review')
    else:
        copied = copy_bound_review(run,output)
        if not copied and not any((replacements, additions, provider_replacements,
                                   provider_additions, canonical_dataset)):
            copy_projected_review(run,output,[])
    review_required = dataset.get('comparison', {}).get(
        'require_independent_result_review', False)
    review_pending = review_required and not (output/'result-review.json').is_file()
    canonical_review_policy = bool(canonical and
        canonical.get('comparison', {}).get('require_independent_result_review') is True)
    # A changed matrix cannot inherit the old review.  Keep the canonical gate
    # on the staged cohort so the normal review installer can bind these exact
    # bytes, then bind the inherited scoring history after that review exists.
    if not (review_pending and canonical_review_policy
            and (output/'scoring-revisions.json').is_file()):
        bind_assembled_scoring_revision(output, source=run)
    if speed_cohort:
        if not (speed_cohort/'speed-comparison.json').is_file():
            raise ValueError('Speed cohort has not been packaged')
        speed_dataset=json.loads((speed_cohort/'dataset.json').read_text())
        speed_rows=read_records(speed_cohort)
        speed_execution=json.loads((speed_cohort/'execution.json').read_text())
        speed_batches=speed_execution.get('batches')
        if not isinstance(speed_batches,list):
            raise ValueError('Speed cohort execution has no batches')
        _copy_speed_bundle(speed_cohort,output/'speed-cohort',speed_dataset,
                           speed_rows,speed_batches,excluded_providers=exclude_providers)
    if resource_cohort:
        from .resource_cohort import validate_cohort
        resource_dataset, resource_rows, resource_batches = validate_cohort(resource_cohort)
        if {row['provider'] for row in resource_rows} & set(exclude_providers):
            raise ValueError('Excluded providers remain in the resource cohort')
        copy_cohort_evidence(resource_cohort, output/'resource-cohort',
                             resource_dataset, resource_rows, resource_batches,
                             summary_name='resource-comparison.json', include_private=True)
    if not failed:require_verified_reference_checks(batches,cases=dataset,records=records,source=output,
        equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
        'equivalent_reference_bytes_with_independent_batch_captures',
        # A pair-only provider rerun is already scored against the current
        # canonical dataset.  It must not make an inherited, provenance-bound
        # scoring correction invalid merely because the fresh call bundle has
        # no scoring-revision receipt of its own.  The receipt belongs to the
        # baseline rows whose original certified batches predate that scorer.
        allow_scoring_rule_revision=has_bound_direct_scoring_revision(run))
    print('Assembled',len(records),'calls; publication:',dataset['comparison']['publication_state'],'unverified:',failed)


def assemble(run, certificates, replacements, output, additions=None, provider_replacements=None,
             provider_additions=None, speed_cohort=None, resource_cohort=None,
             canonical_dataset=None, retain_unverified=False, exclude_providers=None,
             provider_replacement_scope_receipts=None):
    """Build and verify in a sibling staging directory, then publish atomically."""
    sources = [run, *(replacements or []), *(additions or []),
               *(provider_replacements or []), *(provider_additions or []),
               *([speed_cohort] if speed_cohort else []),
               *([resource_cohort] if resource_cohort else [])]
    if certificates is not None:
        sources.append(certificates)
    for source in sources:
        reject_evidence_symlinks(source)
        for name in ('dataset.json', 'results.jsonl', 'manifest.json', 'execution.json'):
            contained_path(source, name, reject_symlinks=True)
    if output.exists():raise ValueError('Destination exists; evidence must not be overwritten')
    output.parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='.'+output.name+'-',dir=output.parent))
    staging=work/'evidence'
    try:
        _assemble(run,certificates,replacements,staging,additions=additions,
            provider_replacements=provider_replacements,provider_additions=provider_additions,
            speed_cohort=speed_cohort,resource_cohort=resource_cohort,
            canonical_dataset=canonical_dataset,
            retain_unverified=retain_unverified,exclude_providers=exclude_providers,
            provider_replacement_scope_receipts=provider_replacement_scope_receipts)
        staging.replace(output)
        work.rmdir()
    except BaseException:
        shutil.rmtree(work,ignore_errors=True)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--certificates',type=Path,help='Certificate tree required only for unfinalized fresh-reference physical runs')
    parser.add_argument('--replacements',type=Path,nargs='*',default=[])
    parser.add_argument('--additions',type=Path,nargs='*',default=[])
    parser.add_argument('--provider-replacements',type=Path,nargs='*',default=[])
    parser.add_argument('--provider-replacement-scope-receipts',type=Path,nargs='*',default=[],help='Hash-bound exact pair scopes for broader provider replacement bundles')
    parser.add_argument('--provider-additions',type=Path,nargs='*',default=[])
    parser.add_argument('--speed-cohort',type=Path)
    parser.add_argument('--resource-cohort',type=Path)
    parser.add_argument('--canonical-dataset',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--retain-unverified',action='store_true',help='Retain a blocked diagnostic cohort; the report builder will still reject it')
    parser.add_argument('--exclude-providers',nargs='*',default=[],help='Explicitly omit providers from the assembled publication and speed matrices; source calls and adapters remain unchanged')
    args=parser.parse_args(argv)
    assemble(args.run_dir,args.certificates,args.replacements,args.output,
        additions=args.additions,provider_replacements=args.provider_replacements,
        provider_replacement_scope_receipts=args.provider_replacement_scope_receipts,
        provider_additions=args.provider_additions,
        speed_cohort=args.speed_cohort,resource_cohort=args.resource_cohort,
        canonical_dataset=args.canonical_dataset,
        retain_unverified=args.retain_unverified,exclude_providers=args.exclude_providers)
