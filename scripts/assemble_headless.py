"""Assemble exact headless measurements, never selecting retries by score."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.reference_lifecycle import (
    require_verified_reference_checks, requires_fresh_reference,
    valid_scoring_revision, reference_revision_cases, scoring_revision_history_sha256,
)
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.result_review import require_result_review
from harness.web_fetch.artifacts import (
    materialize_private_artifacts, record_private_bindings,
    verify_record_private_artifacts,
)
from scripts.run_benchmark import artifact_bindings


ALLOWED_REVISION_FIELDS = {
    'body_requirements', 'required_patterns', 'required_facts', 'acceptance_contract',
    'forbidden_any', 'expected_resources', 'resource_output_contract', 'task_prompt',
}


def read_records(root):
    return [json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]


def copy_bound_review(source, output):
    """Carry a prior review only when its immutable inputs remain byte-identical."""
    prior=source/'result-review.json'
    if not prior.is_file():return False
    review=json.loads(prior.read_text())
    bindings=review.get('bindings',{})
    if (bindings.get('dataset_sha256') != hashlib.sha256((output/'dataset.json').read_bytes()).hexdigest()
            or bindings.get('results_sha256') != hashlib.sha256((output/'results.jsonl').read_bytes()).hexdigest()):
        return False
    shutil.copy2(prior,output/'result-review.json')
    return True


def bind_assembled_scoring_revision(output):
    """Bind inherited scoring history to exact assembled bytes and full blind review."""
    path=output/'scoring-revisions.json'
    review_path=output/'result-review.json'
    if not path.is_file() or not review_path.is_file():return False
    dataset=json.loads((output/'dataset.json').read_text())
    records=read_records(output)
    require_result_review(output,dataset,records)
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
    history.append({
        'kind':'audited_assembled_scoring_revision',
        'scored_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':history[-1]['output_dataset_sha256'],
        'input_results_sha256':history[-1]['output_results_sha256'],
        'output_dataset_sha256':hashlib.sha256((output/'dataset.json').read_bytes()).hexdigest(),
        'output_results_sha256':hashlib.sha256((output/'results.jsonl').read_bytes()).hexdigest(),
        'changed_cases':list(changed.values()),
        'task_decision_changes':list(decisions.values()),
        'provider_calls':0,
        'immutable_call_fields':'attempt identity, output bytes/hash, timestamps, elapsed time, metrics and verdicts retained',
        'source_scoring_receipts_sha256':scoring_revision_history_sha256(history),
        'independent_result_review_sha256':hashlib.sha256(review_path.read_bytes()).hexdigest(),
    })
    path.write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n')
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


def public_manifest(manifest):
    result=deepcopy(manifest)
    result.get('environment',{}).pop('hostname',None)
    result.setdefault('dataset',{})['source']='evaluation/headless/dataset.json (assembled frozen cases)'
    result['provider_config_source']='configs/providers.headless.json'
    if 'projection' in result:
        result['projection'].pop('source_manifest_path',None)
        result['projection']['source_manifest_retained_in_local_run']=True
        if result['projection'].get('kind')=='completed-case-from-live-preflight':
            result['dataset']['case_count']=len(result['reference_checks']['cases'])
    return result


def _without_reference(case):
    value=deepcopy(case)
    value.pop('source_verification',None);value.pop('ground_truth_main_content',None)
    return value


def _provider_request_contract(case, provider):
    """Return the task fields that can affect this provider's physical call.

    Firecrawl records ``minimum_text_fragments`` as unsupported and never puts
    it in the Scrape request.  A cohort frozen immediately before that
    evaluator-only completion diagnostic was added is therefore physically
    equivalent for Firecrawl, while every other task field remains exact.
    """
    value = _without_reference(case)
    if provider == "firecrawl":
        completion = value.get("retrieval_hints", {}).get("completion", {})
        completion.pop("minimum_text_fragments", None)
    return value


def _prune_shadowed_batch_attempts(batches, records):
    """Keep an attempt ID only in the batch whose time bounds contain the selected call.

    Attempt IDs are stable provider/case identities and can recur in a deliberate
    implementation rerun. Time bounds disambiguate the selected physical call.
    """
    by_id={r['attempt_id']:r for r in records}
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
            finished=max(began+timedelta(milliseconds=record['elapsed_ms']),
                datetime.fromisoformat(record.get('completed_at',record['started_at'])))
            if start<=began<=finished<=end:
                kept.append(attempt_id)
        if not kept:
            continue
        batch={**batch,'attempt_ids':kept,'attempt_count':len(kept)}
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


def _copy_speed_bundle(source, target, dataset, rows, batches):
    """Copy only explicitly bound speed evidence into an aggregate tree."""
    if source.is_symlink():
        raise ValueError('Speed cohort root must not be a symlink')
    source_root=source.resolve()
    for child in source.rglob('*'):
        if child.is_symlink():
            raise ValueError('Speed cohort contains a symlink: '+str(child.relative_to(source)))

    allowed={
        'dataset.json','results.jsonl','execution.json','speed-comparison.json',
    }
    for optional in ('result-review.json','scoring-revisions.json','reference-revisions.json'):
        if (source/optional).is_file():allowed.add(optional)
    for row in rows:
        for relative,digest in artifact_bindings(row):
            artifact=contained_path(source,relative)
            if (not artifact.is_file()
                    or hashlib.sha256(artifact.read_bytes()).hexdigest()!=digest):
                raise ValueError('Speed artifact changed: '+relative)
            allowed.add(relative)
        verify_record_private_artifacts(source,row)
        allowed.update(binding['path'] for binding in record_private_bindings(row))
    for batch in batches:
        evidence=batch.get('reference_checks',{}).get('postcheck',{}).get('evidence_path')
        if evidence:allowed.add(evidence)
    for case in dataset.get('cases',[]):
        case_id=case['id']
        source_dir=contained_path(source,'sources/'+case_id)
        if source_dir.is_dir():
            allowed.update(child.relative_to(source_root).as_posix()
                           for child in source_dir.rglob('*') if child.is_file())
        reference=contained_path(source,'list-references/'+case_id+'.json')
        if reference.is_file():allowed.add(reference.relative_to(source_root).as_posix())

    target.mkdir(parents=True)
    for relative in sorted(allowed):
        source_file=contained_path(source,relative)
        if not source_file.is_file():
            raise ValueError('Speed cohort allowlisted file is missing: '+relative)
        target_file=contained_path(target,relative)
        target_file.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source_file,target_file)


def _assemble(run, certificates, replacements, output, additions=None, provider_replacements=None,
              provider_additions=None, speed_cohort=None, canonical_dataset=None, retain_unverified=False):
    additions = additions or []
    provider_replacements = provider_replacements or []
    provider_additions = provider_additions or []
    if output.exists():raise ValueError('Destination exists; evidence must not be overwritten')
    dataset=json.loads((run/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases']}
    canonical=(json.loads(canonical_dataset.read_text()) if canonical_dataset else None)
    canonical_cases={c['id']:c for c in canonical['cases']} if canonical else {}
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
    source_manifest = (json.loads((run/'manifest.json').read_text())
                       if (run/'manifest.json').exists() else {})
    assembled_input=((run/'execution.json').exists() and
        (not source_manifest or source_manifest.get('projection',{}).get('kind') ==
         'complete-provider-from-retained-incomplete-matrix'))
    replacement_cases={};batches=[];evidence=[];selected=[];audit=[]
    if assembled_input:
        prior_batches=json.loads((run/'execution.json').read_text())['batches']
        require_verified_reference_checks(prior_batches,cases=dataset,records=original,source=run,
            equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
            'equivalent_reference_bytes_with_independent_batch_captures',
            allow_scoring_rule_revision=valid_scoring_revision(run))
        batches.extend(public_manifest(batch) for batch in prior_batches)
        evidence.append(run)
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
            old_providers=dataset['coverage_plan'][cases[target_id].get('track','web')]
            new_providers=new_dataset['coverage_plan'][case.get('track','web')]
            if set(old_providers)!=set(new_providers):
                raise ValueError('Replacement provider scope changed: '+cid)
            expected-={(target_id,p) for p in old_providers}
            expected|={(cid,p) for p in new_providers}
            audit.append({'case_id':cid,'replaces_case_id':target_id,'selection_reason':('canonical task/reference correction with a complete matched cohort; no score-based selection'
                if before!=after else 'fresh complete matched cohort after failed reference certificate; no score-based selection'),
                'excluded_attempts':[{k:r[k] for k in ('attempt_id','run_id','started_at','elapsed_ms')} for r in original if r['case_id']==target_id]})
        batches.extend(public_manifest(batch) for batch in replacement_batches);evidence.append(root)
        selected.extend((r,root) for r in rows)
    for cid,case in cases.items():
        if cid in replacement_cases:continue
        rows=[r for r in original if r['case_id']==cid]
        if requires_fresh_reference(canonical_cases.get(cid,case)) and not assembled_input:
            cert=certificates/cid
            manifest=json.loads((cert/'manifest.json').read_text())
            if {r['attempt_id']:r for r in read_records(cert)}!={r['attempt_id']:r for r in rows}:
                raise ValueError('Certificate records differ from original call records: '+cid)
            batches.append(public_manifest(manifest));evidence.append(cert)
        selected.extend((r,run) for r in rows)
    stable=[r for r in original if not requires_fresh_reference(canonical_cases.get(r['case_id'],cases[r['case_id']])) and r['case_id'] not in replacement_cases]
    if stable and not assembled_input:
        manifest=json.loads((run/'manifest.json').read_text())
        manifest.pop('reference_checks',None)  # only non-snapshot cases in this projection
        manifest.update(attempt_count=len(stable),attempt_ids=[r['attempt_id'] for r in stable])
        manifest['dataset']['case_count']=len({r['case_id'] for r in stable})
        manifest['projection']={'kind':'non-snapshot-cases-only','source_manifest_sha256':hashlib.sha256((run/'manifest.json').read_bytes()).hexdigest()}
        batches.append(public_manifest(manifest))
    for root in additions:
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        manifest=json.loads((root/'manifest.json').read_text())
        require_verified_reference_checks(manifest,cases=new_dataset,records=rows,source=root)
        incoming={c['id']:c for c in new_dataset['cases']}
        if set(incoming)&set(cases):raise ValueError('Addition overlaps existing cases')
        planned={(c['id'],p) for c in incoming.values() for p in new_dataset['coverage_plan'][c.get('track','web')]}
        if {(r['case_id'],r['provider']) for r in rows}!=planned:raise ValueError('Addition has incomplete coverage')
        for cid,case in incoming.items():
            if canonical and _without_reference(case)!=_without_reference(canonical_cases[cid]):
                raise ValueError('Addition differs from canonical task rules: '+cid)
            cases[cid]=case
            dataset['coverage_plan'].setdefault(case.get('track','web'),new_dataset['coverage_plan'][case.get('track','web')])
        expected|=planned
        selected.extend((r,root) for r in rows)
        batches.append(public_manifest(manifest));evidence.append(root)
        audit.extend({'case_id':cid,'selection_reason':'missing case completed as a full five-method addition; no score-based selection','excluded_attempts':[]} for cid in incoming)
    pair_replacements = {}
    for root in provider_replacements:
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        replacement_batches=read_verified_batches(root,new_dataset,rows)
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
            old=next(r for r,_ in selected if (r['case_id'],r['provider'])==key)
            audit.append({'case_id':row['case_id'],'provider':row['provider'],
                'selection_reason':'predeclared provider implementation replacement; complete scope selected without score-based retries',
                'excluded_attempts':[{k:old[k] for k in ('attempt_id','run_id','started_at','elapsed_ms')}]})
        batches.extend(public_manifest(batch) for batch in replacement_batches);evidence.append(root)
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
        batches.extend(public_manifest(batch) for batch in addition_batches);evidence.append(root)
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
    batches=_prune_shadowed_batch_attempts(batches,records)
    validate_report_ids(dataset,records)
    if len(records)!=len(expected) or {(r['case_id'],r['provider']) for r in records}!=expected:
        raise ValueError('Assembled coverage differs from the complete cohort')
    if provider_replacements:
        dataset['comparison']['reference_binding_mode']='equivalent_reference_bytes_with_independent_batch_captures'
    for track in dataset.get('coverage_plan',{}):
        dataset['coverage_plan'][track]=list(dict.fromkeys(dataset['coverage_plan'][track]+sorted(added_providers)))
    if added_providers:
        dataset.setdefault('comparison',{})['require_independent_result_review']=True
    failed=[cid for batch in batches for cid,row in batch.get('reference_checks',{}).get('postcheck',{}).get('cases',{}).items() if row['status']!='verified']
    dataset['comparison']['publication_state']='blocked_reference_checks' if failed else 'verified'
    dataset['comparison']['unverified_reference_cases']=sorted(set(failed))
    has_reference_revisions=any(reference_revision_cases(root) for root in evidence)
    if not retain_unverified and not has_reference_revisions:
        require_verified_reference_checks(batches,cases=dataset,records=records,
            equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
            'equivalent_reference_bytes_with_independent_batch_captures',
            allow_scoring_rule_revision=valid_scoring_revision(run))
    output.mkdir(parents=True)
    source_root=canonical_dataset.parent if canonical_dataset else run
    for folder in ('sources','list-references'):
        shutil.copytree(source_root/folder,output/folder)
    for cid,(_,root) in replacement_cases.items() if not canonical else []:
        # This is a newly created assembly tree, not previously measured output.
        target=output/'sources'/cid
        shutil.copytree(root/'sources'/cid,target,dirs_exist_ok=True)
    for row,root in selected:
        paths=[(row['artifact_path'],row['metrics']['content_sha256'])]
        meta=row.get('provider_metadata',{})
        for path_key,hash_key in [('rendered_html_path','rendered_html_sha256'),('raw_stdout_path','raw_stdout_sha256')]:
            if meta.get(path_key):paths.append((meta[path_key],meta[hash_key]))
        for delivered in meta.get('file_evaluation', {}).get('files', []):
            if delivered.get('artifact_path'):
                paths.append((delivered['artifact_path'], delivered['sha256']))
        for relative,digest in paths:
            source=contained_path(root,relative)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:raise ValueError('Artifact changed: '+relative)
            target=contained_path(output,relative);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
        materialize_private_artifacts(output, root, [row])
    for root in evidence:
        if (root/'reference-checks').exists():
            shutil.copytree(root/'reference-checks',output/'reference-checks',dirs_exist_ok=True)
    # Keep rejected calls, scores and certificates available outside rankings.
    for cid in replacement_cases:
        target=output/'diagnostics'/'replaced-cases'/cid;target.mkdir(parents=True)
        rows=[r for r in original if r['case_id']==cid]
        # JSONL readers split on Unicode line separators too. Binary/image
        # diagnostics can contain NEL/U+2028, so keep every record ASCII-safe.
        (target/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=True)+'\n' for r in rows))
        certificate=certificates/cid
        if certificate.exists():
            rejected=public_manifest(json.loads((certificate/'manifest.json').read_text()))
            (target/'manifest.json').write_text(json.dumps(rejected,ensure_ascii=False,indent=2)+'\n')
        elif (run/'manifest.json').exists():
            rejected=public_manifest(json.loads((run/'manifest.json').read_text()))
            rejected.pop('reference_checks',None)
            rejected.update(attempt_count=len(rows),attempt_ids=[r['attempt_id'] for r in rows])
            rejected['projection']={'kind':'replaced-non-snapshot-case','source_manifest_sha256':hashlib.sha256((run/'manifest.json').read_bytes()).hexdigest()}
            (target/'manifest.json').write_text(json.dumps(rejected,ensure_ascii=False,indent=2)+'\n')
        else:
            rejected_batches=_prune_shadowed_batch_attempts(
                [public_manifest(batch) for batch in prior_batches],rows)
            if not rejected_batches:
                raise ValueError('Assembled input has no execution batch for replaced case: '+cid)
            (target/'execution.json').write_text(json.dumps(
                {'batches':rejected_batches},ensure_ascii=False,indent=2)+'\n')
        if (certificate/'reference-checks').exists():
            shutil.copytree(certificate/'reference-checks',target/'reference-checks')
        for row in rows:
            meta=row.get('provider_metadata',{})
            for relative in filter(None,[row['artifact_path'],meta.get('rendered_html_path'),meta.get('raw_stdout_path')]):
                dest=contained_path(target,relative);dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(contained_path(run,relative),dest)
    for name,payload in [('dataset.json',dataset),('execution.json',{'batches':batches}),('selection-audit.json',audit)]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    (output/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=True)+'\n' for r in records))
    if (run/'scoring-revisions.json').is_file():
        shutil.copy2(run/'scoring-revisions.json',output/'scoring-revisions.json')
    # Consolidate independently validated source reference-correction receipts
    # and bind them to the exact assembled dataset/results pair.
    revision_rows=[]
    for root in dict.fromkeys(evidence):
        covered=reference_revision_cases(root)
        if not covered:continue
        receipts=json.loads((root/'reference-revisions.json').read_text())
        for receipt in receipts:
            if (receipt.get('output_dataset_sha256') != hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest()
                    or receipt.get('output_results_sha256') != hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()):
                continue
            revision_rows.extend(deepcopy(receipt.get('changed_cases',[])))
    if revision_rows:
        unique={(row['case_id'],row['old_reference_sha256'],row['new_reference_sha256']):row
                for row in revision_rows}
        consolidated={
            'kind':'audited_reference_correction',
            'corrected_at':datetime.now(timezone.utc).isoformat(),
            'output_dataset_sha256':hashlib.sha256((output/'dataset.json').read_bytes()).hexdigest(),
            'output_results_sha256':hashlib.sha256((output/'results.jsonl').read_bytes()).hexdigest(),
            'changed_cases':list(unique.values()),
            'task_decision_changes':[],
            'provider_calls':0,
            'immutable_call_fields':'attempt identity, output bytes/hash, timestamps and elapsed time retained',
            'source_receipts_sha256':sorted({hashlib.sha256((root/'reference-revisions.json').read_bytes()).hexdigest()
                                             for root in dict.fromkeys(evidence)
                                             if (root/'reference-revisions.json').is_file()}),
        }
        (output/'reference-revisions.json').write_text(json.dumps([consolidated],ensure_ascii=False,indent=2)+'\n')
    copy_bound_review(run,output)
    bind_assembled_scoring_revision(output)
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
                           speed_rows,speed_batches)
    if not failed:require_verified_reference_checks(batches,cases=dataset,records=records,source=output,
        equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
        'equivalent_reference_bytes_with_independent_batch_captures')
    print('Assembled',len(records),'calls; publication:',dataset['comparison']['publication_state'],'unverified:',failed)


def assemble(run, certificates, replacements, output, additions=None, provider_replacements=None,
             provider_additions=None, speed_cohort=None, canonical_dataset=None, retain_unverified=False):
    """Build and verify in a sibling staging directory, then publish atomically."""
    if output.exists():raise ValueError('Destination exists; evidence must not be overwritten')
    output.parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='.'+output.name+'-',dir=output.parent))
    staging=work/'evidence'
    try:
        _assemble(run,certificates,replacements,staging,additions=additions,
            provider_replacements=provider_replacements,provider_additions=provider_additions,
            speed_cohort=speed_cohort,canonical_dataset=canonical_dataset,
            retain_unverified=retain_unverified)
        staging.replace(output)
        work.rmdir()
    except BaseException:
        shutil.rmtree(work,ignore_errors=True)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--certificates',type=Path,required=True)
    parser.add_argument('--replacements',type=Path,nargs='*',default=[])
    parser.add_argument('--additions',type=Path,nargs='*',default=[])
    parser.add_argument('--provider-replacements',type=Path,nargs='*',default=[])
    parser.add_argument('--provider-additions',type=Path,nargs='*',default=[])
    parser.add_argument('--speed-cohort',type=Path)
    parser.add_argument('--canonical-dataset',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--retain-unverified',action='store_true',help='Retain a blocked diagnostic cohort; the report builder will still reject it')
    args=parser.parse_args()
    assemble(args.run_dir,args.certificates,args.replacements,args.output,
        additions=args.additions,provider_replacements=args.provider_replacements,
        provider_additions=args.provider_additions,
        speed_cohort=args.speed_cohort,canonical_dataset=args.canonical_dataset,retain_unverified=args.retain_unverified)
