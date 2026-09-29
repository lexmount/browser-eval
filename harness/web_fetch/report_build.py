"""Build the single current report from an explicit evidence directory."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from harness.reporting.assets import copy_bound_assets
from .report import generate, generate_public_benchmark_research, generate_webmainbench, generate_webmainbench_full, webmainbench_full_machine_issue, generate_wcxb, succeeded, measured, failure_category, TRACKS
from .report_changes import webmainbench_history
from .failure_diagnosis import diagnose
from .safety import contained_path, safe_identifier, validate_report_ids
from .reference_lifecycle import require_verified_reference_checks
from .reliability import assess_reliability
from .storage import require_selection_history,data_home
from .wcxb_latency import load_full_paired_latency
from .scoring import SCORING_SOURCE_SHA256
from .webmainbench_diagnostics import validate_teds_revision_if_present
from .webmainbench_latency_correction import prior_results_path
from .webmainbench_latency_diagnostic import load_diagnostic
from .artifacts import (
    diagnostic_log_binding, record_public_call_log_bindings, verify_record_private_artifacts,
    verify_public_call_log, verify_record_public_call_logs,
)
def require_risk_control_certification(source: Path, dataset: dict, records: list[dict]) -> None:
    from .risk_control import validate_risk_control_certification

    """Validate the complete profile-bound risk-control cohort before reporting."""
    comparison = dataset.get("comparison", {})
    if comparison.get("kind") != "risk_control_100_moli":
        return
    path = contained_path(source, "certification.json")
    if not path.is_file() or path.is_symlink():
        raise ValueError("Risk-control report requires a certification receipt")
    profile_path = Path(__file__).resolve().parents[2] / "evaluation/risk-control/run-profile.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    validate_risk_control_certification(source, dataset, records, receipt, profile_path)
    for record in records:
        metadata = record.get("provider_metadata", {})
        if (diagnostic_log_binding(metadata) is None
                or metadata.get("diagnostic_log_truncated") is not False):
            raise ValueError("Risk-control certification requires complete diagnostic logs")


def scoring_scope(records, cases):
    scopes = {'stable_contract' if 'stable_task_pass' in r['metrics'] else
              'list_identity' if cases[r['case_id']].get('task_kind') == 'structure' else
              cases[r['case_id']].get('task_kind', 'body') for r in records}
    return next(iter(scopes)) if len(scopes) == 1 else 'mixed'


def copy_methodology_assets(output, assets=None):
    assets = assets or Path(__file__).parent/'assets/methodology'
    for name in ('annotation.png', 'validation.png', 'scoring.png'):
        source = assets/name
        if source.is_file():
            target = output/'assets/methodology'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def copy_speed_cohort_public_files(source, target, dataset, records, batches):
    """Copy the exact public speed-cohort evidence set; reject every symlink."""
    source = Path(source)
    if source.is_symlink():
        raise ValueError('Speed cohort root must not be a symlink')
    source_root = source.resolve()
    for child in source.rglob('*'):
        if child.is_symlink():
            raise ValueError('Speed cohort evidence must not contain symlinks')

    allowed = {'dataset.json', 'results.jsonl', 'speed-comparison.json'}
    for optional in ('execution.json', 'manifest.json', 'scoring-revisions.json',
                     'reference-revisions.json'):
        if (source / optional).is_file():
            allowed.add(optional)
    for record in records:
        relative = record['artifact_path']
        artifact = contained_path(source, relative)
        expected = record.get('metrics', {}).get('content_sha256')
        if (not artifact.is_file() or not expected
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected):
            raise ValueError('Speed cohort output integrity failure: ' + record['attempt_id'])
        allowed.add(relative)
        for binding in record_public_call_log_bindings(record):
            allowed.add(verify_public_call_log(source, binding).relative_to(source_root).as_posix())
    for batch in batches:
        relative = batch.get('reference_checks', {}).get('postcheck', {}).get('evidence_path')
        if relative:
            allowed.add(relative)
    for case in dataset.get('cases', []):
        case_id = case['id']
        source_dir = contained_path(source, 'sources/' + case_id)
        if source_dir.is_dir():
            allowed.update(child.relative_to(source_root).as_posix()
                           for child in source_dir.rglob('*') if child.is_file())
        reference = contained_path(source, 'list-references/' + case_id + '.json')
        if reference.is_file():
            allowed.add(reference.relative_to(source_root).as_posix())

    target.mkdir(parents=True)
    for relative in sorted(allowed):
        source_file = contained_path(source, relative)
        if not source_file.is_file():
            raise ValueError('Speed cohort allowlisted file is missing: ' + relative)
        target_file = contained_path(target, relative)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)


def build_public_benchmark_research(source: Path, output: Path, *, include_english: bool = False) -> Path:
    """Build the research-only companion without measured evidence."""
    source, output = source.resolve(), output.resolve()
    marker = '.generated-report'
    if output.exists() and not (output / marker).is_file():
        raise ValueError(f'Refusing to replace an unmarked directory: {output}')
    research = json.loads(source.read_text(encoding='utf-8'))
    if research.get('schema_version') != 1:
        raise ValueError('Unsupported public benchmark research schema')
    sample_path = contained_path(source.parent.parent, research['sampling_contract']['manifest'])
    sample = None
    if sample_path.is_file():
        sample = json.loads(sample_path.read_text(encoding='utf-8'))
        research['frozen_sample'] = {
            'parent': sample['parent'],
            'sampling': sample['sampling'],
            'quality_audit': sample['quality_audit'],
            'parent_distribution': sample['parent_distribution'],
            'sample_distribution': sample['sample_distribution'],
            'case_count': len(sample['cases']),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.research-report-build-', dir=output.parent))
    try:
        generate_public_benchmark_research(staging, research, include_english=include_english)
        if sample is not None:
            shutil.copy2(sample_path, staging / 'sample-manifest.json')
        (staging / marker).write_text('generated\n', encoding='utf-8')
        backup = output.with_name(output.name + '.previous')
        if backup.exists():
            shutil.rmtree(backup)
        if output.exists():
            output.rename(backup)
        staging.rename(output)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output / 'index.html'


def build_report(source: Path, output: Path, *, include_english: bool = False,
                 local_audit: bool = False) -> Path:
    source, output = source.resolve(), output.resolve()
    if source == output or output in source.parents:
        raise ValueError('Report output must not contain its evidence source')
    marker = '.generated-report'
    if output.exists() and not (output/marker).is_file():
        raise ValueError(f'Refusing to replace an unmarked directory: {output}')
    dataset_probe = json.loads(contained_path(source, 'dataset.json').read_text())
    if dataset_probe.get('comparison', {}).get('kind') == 'webmainbench_frozen_html_to_markdown':
        return build_webmainbench_report(source, output, include_english=include_english)
    if not local_audit:
        require_selection_history(source)
    dataset = json.loads(contained_path(source,'dataset.json').read_text())
    records = [json.loads(line) for line in contained_path(source,'results.jsonl').read_text().splitlines()]
    validate_report_ids(dataset,records)
    require_risk_control_certification(source, dataset, records)
    cases = {c['id']:c for c in dataset['cases']}
    from .result_review import require_result_review
    if local_audit:
        from .local_audit import require_local_audit
        result_review = require_local_audit(source, dataset, records)
    else:
        result_review = require_result_review(source, dataset, records)
    speed_entry=source/'speed-cohort'
    if speed_entry.is_symlink():
        raise ValueError('Speed cohort root must not be a symlink')
    speed_root=contained_path(source,'speed-cohort')
    speed_dataset=None
    speed_batches=[]
    if speed_root.exists():
        speed=json.loads((speed_root/'speed-comparison.json').read_text())
        if speed.get('bindings',{}).get('dataset_sha256') != hashlib.sha256((speed_root/'dataset.json').read_bytes()).hexdigest():
            raise ValueError('Speed comparison dataset binding mismatch')
        if speed.get('bindings',{}).get('results_sha256') != hashlib.sha256((speed_root/'results.jsonl').read_bytes()).hexdigest():
            raise ValueError('Speed comparison result binding mismatch')
        speed_dataset=json.loads((speed_root/'dataset.json').read_text())
        speed_records=[json.loads(line) for line in (speed_root/'results.jsonl').read_text().splitlines()]
        speed_execution=speed_root/'execution.json'
        if speed_execution.is_file():
            speed_batches=json.loads(speed_execution.read_text()).get('batches', [])
        for row in speed_records:
            verify_record_private_artifacts(speed_root, row)
            verify_record_public_call_logs(speed_root, row)
            raw=contained_path(speed_root,row['artifact_path'])
            if hashlib.sha256(raw.read_bytes()).hexdigest()!=row['metrics']['content_sha256']:
                raise ValueError('Speed cohort output integrity failure: '+row['attempt_id'])
    resource_root=source/'resource-cohort'
    if resource_root.exists():
        resource=json.loads((resource_root/'resource-comparison.json').read_text())
        for name in ('dataset','results'):
            extension='json' if name == 'dataset' else 'jsonl'
            raw=(resource_root/f'{name}.{extension}').read_bytes()
            if resource.get('bindings',{}).get(name+'_sha256') != hashlib.sha256(raw).hexdigest():
                raise ValueError('Resource comparison '+name+' binding mismatch')
        resource_dataset=json.loads((resource_root/'dataset.json').read_text())
        resource_records=[json.loads(line) for line in
                          (resource_root/'results.jsonl').read_text().splitlines()]
        require_result_review(resource_root,resource_dataset,resource_records)
        for row in resource_records:
            raw=contained_path(resource_root,row['artifact_path'])
            if hashlib.sha256(raw.read_bytes()).hexdigest()!=row['metrics']['content_sha256']:
                raise ValueError('Resource cohort output integrity failure: '+row['attempt_id'])
    for c in cases.values():
        annotation=c.get('source_benchmark',{}).get('stable_annotation',{})
        if annotation.get('anchor_evidence_sha256'):
            if local_audit:
                # The two historical dynamic cohorts retain their own source
                # audit receipts.  Do not reinterpret those revisions with the
                # newer origin/main release gate in a local merged view.
                continue
            from copy import deepcopy
            from .stable_annotations import bind_annotation_evidence
            checked=deepcopy(c)
            bind_annotation_evidence(checked,source)
            if checked['source_benchmark']['stable_annotation']['anchor_evidence_sha256'] != annotation['anchor_evidence_sha256']:
                raise ValueError('Stable text evidence changed: '+c['id'])
    for r in records:
        if r.get('metrics', {}).get('scoring_source_sha256') != SCORING_SOURCE_SHA256:
            raise ValueError(
                f'Stale scoring implementation; rescore before reporting: {r["attempt_id"]}')
        contract = cases[r['case_id']].get('acceptance_contract')
        if contract:
            from .stable_tasks import validate_acceptance_contract
            validate_acceptance_contract(contract)
            expected = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if r['metrics'].get('acceptance_contract_sha256') != expected:
                raise ValueError(f'Acceptance contract hash missing or mismatched: {r["attempt_id"]}')
        composite = cases[r['case_id']].get('composite_acceptance')
        if composite:
            from .composite_tasks import validate_composite_acceptance
            validate_composite_acceptance(composite)
            expected = hashlib.sha256(json.dumps(
                composite, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if r['metrics'].get('composite_acceptance_sha256') != expected:
                raise ValueError(f'Composite contract hash missing or mismatched: {r["attempt_id"]}')
        body_requirements = cases[r['case_id']].get('body_requirements')
        if body_requirements:
            expected = hashlib.sha256(json.dumps(
                body_requirements, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if r['metrics'].get('body_requirements_sha256') != expected:
                raise ValueError(f'Body contract hash missing or mismatched: {r["attempt_id"]}')
        if cases[r['case_id']].get('expected_resources') or cases[r['case_id']].get('resource_output_contract'):
            resource_contract = {
                'expected_resources': cases[r['case_id']].get('expected_resources', []),
                'resource_output_contract': cases[r['case_id']].get('resource_output_contract', {}),
            }
            expected = hashlib.sha256(json.dumps(
                resource_contract, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if r['metrics'].get('resource_acceptance_contract_sha256') != expected:
                raise ValueError(
                    f'Resource acceptance contract hash missing or mismatched: {r["attempt_id"]}')
    diagnostic_path=Path(__file__).resolve().parents[2]/'research/failure-sources.json'
    source_checks={x['case_id']:x for x in json.loads(diagnostic_path.read_text())} if diagnostic_path.exists() else {}
    if not cases or not records:
        raise ValueError('A report requires tasks and measured records')
    execution = source/'execution.json'
    manifests = json.loads(execution.read_text())['batches'] if execution.exists() else [json.loads((source/'manifest.json').read_text())]
    if not local_audit:
        require_verified_reference_checks(manifests, cases=dataset, records=records, source=source,
            equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
            'equivalent_reference_bytes_with_independent_batch_captures')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.report-build-', dir=output.parent))
    try:
        for r in records:
            # Raw API envelopes remain private persistent evidence.  Verify the
            # binding here, but deliberately do not copy them into staging.
            verify_record_private_artifacts(source, r)
            path = Path(r['artifact_path'])
            raw = contained_path(source,path)
            if hashlib.sha256(raw.read_bytes()).hexdigest() != r['metrics']['content_sha256']:
                raise ValueError(f'Output integrity failure: {path}')
            target = staging/path; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw, target)
            meta = r.get('provider_metadata', {})
            if meta.get('rendered_html_path'):
                html_source = contained_path(source, meta['rendered_html_path'])
                if hashlib.sha256(html_source.read_bytes()).hexdigest() != meta.get('rendered_html_sha256'):
                    raise ValueError('Rendered HTML integrity failure: ' + r['attempt_id'])
                html_target = contained_path(staging, meta['rendered_html_path'])
                html_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(html_source, html_target)
            if meta.get('raw_stdout_path'):
                binary_source = contained_path(source, meta['raw_stdout_path'])
                if hashlib.sha256(binary_source.read_bytes()).hexdigest() != meta.get('raw_stdout_sha256'):
                    raise ValueError('Raw stdout integrity failure: ' + r['attempt_id'])
                binary_target = contained_path(staging, meta['raw_stdout_path'])
                binary_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(binary_source, binary_target)
            for file in r.get('provider_metadata', {}).get('file_evaluation', {}).get('files', []):
                binary = contained_path(source, file['artifact_path'])
                if hashlib.sha256(binary.read_bytes()).hexdigest() != file['sha256']:
                    raise ValueError('File output integrity failure: '+r['attempt_id'])
                target_file = contained_path(staging, file['artifact_path'])
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(binary, target_file)
            for binding in record_public_call_log_bindings(r):
                log_source = verify_public_call_log(source, binding)
                call_log = json.loads(log_source.read_text())
                call_output = call_log['output']
                call_output_source = contained_path(source, call_output['path'])
                call_output_target = contained_path(staging, call_output['path'])
                call_output_target.parent.mkdir(parents=True, exist_ok=True)
                if call_output_target.exists():
                    if hashlib.sha256(call_output_target.read_bytes()).hexdigest() != call_output['sha256']:
                        raise ValueError('Public call-log output collision: ' + call_output['path'])
                else:
                    shutil.copy2(call_output_source, call_output_target)
                log_target = contained_path(staging, binding['path'])
                log_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(log_source, log_target)
                verify_public_call_log(staging, binding)
            text=raw.read_text(encoding='utf-8',errors='replace')
            from .title_quality import add_title_quality
            add_title_quality(r, cases[r['case_id']], source)
            saved_diagnosis = r.get('diagnosis')
            if local_audit and (saved_diagnosis or {}).get('classification_revision') == 'failure-classification-v2':
                r['diagnosis'] = saved_diagnosis
            else:
                r['diagnosis']=diagnose(r,cases[r['case_id']],text,source_checks.get(r['case_id']))
            r['failure_category'] = failure_category(r, cases[r['case_id']])
        for folder in ['sources','list-references','reference-checks']:
            tree=contained_path(source,folder)
            if tree.exists():
                for child in tree.rglob('*'):
                    if child.is_symlink():
                        raise ValueError('Evidence trees must not contain symlinks')
        if (source/'sources').exists():
            shutil.copytree(source/'sources', staging/'sources')
        if (source/'list-references').exists():
            shutil.copytree(source/'list-references',staging/'list-references')
        if (source/'reference-checks').exists():
            shutil.copytree(source/'reference-checks', staging/'reference-checks')
        if speed_root.exists():
            copy_speed_cohort_public_files(
                speed_root, staging/'speed-cohort', speed_dataset, speed_records, speed_batches)
        if resource_root.exists():
            shutil.copytree(resource_root,staging/'resource-cohort',ignore=shutil.ignore_patterns(
                'result-review.json','review-history','private_artifacts'))
        summary = {t:{p:{'attempts':len(rs), 'task_pass':sum(succeeded(r,cases[r['case_id']]) for r in rs)}
                      for p in sorted({r['provider'] for r in records})
                      if (rs := [r for r in records if r['provider']==p and cases[r['case_id']].get('track','web')==t])}
                   for t in TRACKS}
        for provider, row in summary.get('challenge', {}).items():
            subset = [r for r in records if r['provider'] == provider
                      and cases[r['case_id']].get('track') == 'challenge']
            body = [r for r in subset if cases[r['case_id']].get('task_kind', 'body') == 'body'
                    and cases[r['case_id']].get('ground_truth_main_content')]
            row['body_evaluated_attempts'] = len(body)
            row['body_task_pass'] = sum(succeeded(r, cases[r['case_id']]) for r in body) if body else None
        for track, providers in summary.items():
            for provider, row in providers.items():
                subset = [r for r in records if r['provider'] == provider and cases[r['case_id']].get('track','web') == track]
                row['scoring_scope'] = scoring_scope(subset, cases)
                row['task_evaluated_attempts'] = sum(measured(r, cases[r['case_id']]) for r in subset)
                row['task_unmeasured_attempts'] = len(subset) - row['task_evaluated_attempts']
                text=[r for r in subset if r['metrics'].get('content_check_coverage') is not None]
                row['text_checked_attempts']=len(text)
                row['text_checked_cases']=len({r['case_id'] for r in text})
        for name,data in [('dataset.json',dataset),('execution.json',{'batches':manifests}),('summary.json',summary)]:
            (staging/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        if (source / 'certification.json').is_file():
            shutil.copy2(source / 'certification.json', staging / 'certification.json')
        reliability=assess_reliability(dataset,records)
        (staging/'reliability.json').write_text(json.dumps(reliability,ensure_ascii=False,indent=2)+'\n')
        # Keep the downloadable result file byte-identical to the independently
        # reviewed evidence. Derived diagnosis/title fields remain report-only.
        (staging/'results.jsonl').write_bytes(contained_path(source,'results.jsonl').read_bytes())
        if result_review is not None:
            (staging/'result-review.json').write_text(json.dumps(result_review,ensure_ascii=False,indent=2)+'\n')
        if local_audit:
            if (source/'local-evidence').is_dir():
                shutil.copytree(source/'local-evidence', staging/'local-evidence')
        copy_methodology_assets(staging)
        provenance=Path(__file__).resolve().parents[2]/'research/headless-implementation-provenance.json'
        if provenance.is_file():
            shutil.copy2(provenance,staging/'implementation-provenance.json')
        if (source/'scoring-revisions.json').is_file():
            scoring_path = source/'scoring-revisions.json'
            scoring_revisions = json.loads(scoring_path.read_text())
            shutil.copy2(scoring_path, staging/'scoring-revisions.json')
            for revision in scoring_revisions:
                relative = revision.get('prior_result_review_path')
                expected = revision.get('prior_result_review_sha256')
                if not relative:
                    continue
                archived = contained_path(source, relative)
                if not archived.is_file() or archived.is_symlink():
                    raise ValueError('Archived result review is missing or unsafe')
                actual = hashlib.sha256(archived.read_bytes()).hexdigest()
                if not expected or actual != expected:
                    raise ValueError('Archived result review hash does not match scoring receipt')
                target = staging/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archived, target)
        if (source/'reference-revisions.json').is_file():
            shutil.copy2(source/'reference-revisions.json', staging/'reference-revisions.json')
        if (source/'latency-corrections.json').is_file():
            shutil.copy2(source/'latency-corrections.json', staging/'latency-corrections.json')
        if (source/'request-status-corrections.json').is_file():
            shutil.copy2(source/'request-status-corrections.json',
                         staging/'request-status-corrections.json')
        if (source/'file-rerun-selection.json').is_file():
            shutil.copy2(source/'file-rerun-selection.json', staging/'file-rerun-selection.json')
            shutil.copytree(source/'history/file-baseline', staging/'history/file-baseline')
        generate(staging,dataset,records,manifests,include_english=include_english)
        (staging/marker).write_text('Generated from persisted evidence. Rebuild; do not edit.\n')
        # Replace only an explicitly marked generated directory; never merge stale pages.
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix='.report-swap-',dir=output.parent))/'report'
            output.rename(backup)
            try: staging.rename(output)
            except BaseException:
                backup.rename(output)
                raise
            shutil.rmtree(backup.parent)
        else: staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    return output/'index.html'


def validate_webmainbench_correction(receipt: dict, dataset: dict) -> None:
    """Reject receipts outside the frozen case and participant scope."""
    if not isinstance(receipt, dict):
        raise ValueError('Invalid fixture correction receipt')
    scope = receipt.get('providers')
    effects = receipt.get('effects')
    if (receipt.get('case_id') not in {case['id'] for case in dataset['cases']}
            or not isinstance(scope, list) or not scope
            or any(not isinstance(name, str) for name in scope)
            or len(set(scope)) != len(scope)
            or not set(scope).issubset(dataset['coverage_plan']['static_html'])
            or not isinstance(effects, dict) or set(effects) != set(scope)):
        raise ValueError('Fixture correction scope does not match the report matrix')
    if any(receipt.get(key) != dataset[key] for key in
           ('sample_manifest_sha256', 'source_dataset_sha256')):
        raise ValueError('Fixture correction is not bound to the current report dataset')


def validate_webmainbench_anomaly_review(receipt: dict, source: Path,
                                         dataset: dict, records: list[dict]) -> None:
    """Bind the compact anomaly review to this exact report matrix."""
    if not isinstance(receipt, dict) or receipt.get('kind') != 'webmainbench_anomaly_root_cause_review':
        raise ValueError('Invalid WebMainBench anomaly review')
    bindings = receipt.get('bindings', {})
    expected_files = {
        'dataset_json_sha256': 'dataset.json',
        'score_summaries_json_sha256': 'score-summaries.json',
    }
    for binding, filename in expected_files.items():
        actual = hashlib.sha256(contained_path(source, filename).read_bytes()).hexdigest()
        if bindings.get(binding) != actual:
            raise ValueError(f'WebMainBench anomaly review {binding} mismatch')
    if bindings.get('results_jsonl_sha256') != hashlib.sha256(
            prior_results_path(source).read_bytes()).hexdigest():
        raise ValueError('WebMainBench anomaly review results_jsonl_sha256 mismatch')
    if (bindings.get('source_dataset_sha256') != dataset.get('source_dataset_sha256')
            or bindings.get('sample_manifest_sha256') != dataset.get('sample_manifest_sha256')):
        raise ValueError('WebMainBench anomaly review dataset binding mismatch')
    retest_sha = receipt.get('diagnostic_evidence', {}).get('moli_origin_retest_summary_sha256')
    if retest_sha:
        retest_path = contained_path(source, 'latency-diagnostic/failure-retest/summary.json')
        if not retest_path.is_file() or hashlib.sha256(retest_path.read_bytes()).hexdigest() != retest_sha:
            raise ValueError('Moli origin-failure retest is not bound to the anomaly review')
    scope = receipt.get('scope', {})
    triggered = receipt.get('triggered_attempt_ids')
    attempts = {row['attempt_id'] for row in records}
    providers = {row['provider'] for row in records}
    cases = receipt.get('cases')
    if (scope.get('logical_results') != len(records)
            or not isinstance(triggered, list)
            or len(triggered) != scope.get('signal_triggered_results')
            or len(set(triggered)) != len(triggered)
            or not set(triggered).issubset(attempts)
            or not isinstance(cases, list)
            or len(cases) != scope.get('distinct_cases')
            or {row.get('case_id') for row in cases} != {
                attempt.split('__', 2)[1] for attempt in triggered
            }
            or any(not isinstance(row.get('affected_providers'), list)
                   or not row['affected_providers']
                   or len(set(row['affected_providers'])) != len(row['affected_providers'])
                   or not set(row['affected_providers']).issubset(providers)
                   or any(not isinstance(row.get(key), str) or not row[key].strip()
                          for key in ('classification', 'observed_zh', 'observed_en',
                                      'cause_zh', 'cause_en'))
                   for row in cases)
            or scope.get('unresolved_triggered_results') != 0):
        raise ValueError('WebMainBench anomaly review scope mismatch')
    disposition = receipt.get('disposition', {})
    if any(disposition.get(key) is not False for key in
           ('score_bearing_results_changed', 'provider_calls_replaced', 'references_changed')):
        raise ValueError('Diagnostic review must not mutate score-bearing evidence')


def build_webmainbench_report(source: Path, output: Path, *, include_english: bool = False) -> Path:
    """Build the current public-benchmark report without legacy task predicates."""
    dataset = json.loads(contained_path(source, 'dataset.json').read_text())
    records = [json.loads(line) for line in contained_path(source, 'results.jsonl').read_text().splitlines() if line.strip()]
    teds_revision = validate_teds_revision_if_present(source, records)
    summaries = json.loads(contained_path(source, 'score-summaries.json').read_text())
    manifests = json.loads(contained_path(source, 'execution.json').read_text())['batches']
    latency_diagnostic = load_diagnostic(source, dataset, records, manifests)
    review_path = source / 'result-review.json'
    review = json.loads(review_path.read_text()) if review_path.is_file() else None
    if review is not None and review.get('bindings', {}).get('results_jsonl_sha256') != hashlib.sha256(
            prior_results_path(source).read_bytes()).hexdigest():
        raise ValueError('WebMainBench result review is not bound to preserved scored evidence')
    research_path = Path(__file__).resolve().parents[2] / 'research/public-web-benchmarks.json'
    research = json.loads(research_path.read_text(encoding='utf-8'))
    adoption_path = Path(__file__).resolve().parents[2] / 'research/provider-adoption.json'
    adoption = json.loads(adoption_path.read_text(encoding='utf-8'))
    corrections_path = Path(__file__).resolve().parents[2] / 'research/webmainbench-corrections.json'
    corrections = json.loads(corrections_path.read_text(encoding='utf-8'))
    anomaly_review_path = Path(__file__).resolve().parents[2] / 'research/webmainbench-anomaly-review.json'
    anomaly_review = json.loads(anomaly_review_path.read_text(encoding='utf-8'))
    release_path = Path(__file__).resolve().parents[2] / 'research/report-changes.json'
    release_history = webmainbench_history(json.loads(release_path.read_text(encoding='utf-8')))
    fixture_correction_path = source / 'fixture-correction.json'
    fixture_correction = json.loads(fixture_correction_path.read_text(encoding='utf-8')) if fixture_correction_path.is_file() else None
    providers = dataset['coverage_plan']['static_html']
    case_ids = {case['id'] for case in dataset['cases']}
    if fixture_correction is not None:
        validate_webmainbench_correction(fixture_correction, dataset)
    validate_webmainbench_anomaly_review(anomaly_review, source, dataset, records)
    if len(case_ids) != 200 or len(records) != len(case_ids) * len(providers):
        raise ValueError('WebMainBench report matrix is incomplete')
    if {(row['provider'], row['case_id']) for row in records} != {
        (provider, case_id) for provider in providers for case_id in case_ids
    }:
        raise ValueError('WebMainBench provider/case matrix mismatch')
    parent = data_home() / 'public-benchmarks/webmainbench-545/WebMainBench_545.jsonl'
    if not parent.is_file() or hashlib.sha256(parent.read_bytes()).hexdigest() != dataset['source_dataset_sha256']:
        raise ValueError('Frozen WebMainBench parent dataset is unavailable or differs from the bound source hash')
    parent_rows = [json.loads(line) for line in parent.read_text(encoding='utf-8').splitlines() if line.strip()]
    source_html = {row['track_id']: row['html'] for row in parent_rows if row['track_id'] in case_ids}
    if set(source_html) != case_ids:
        raise ValueError('Frozen source HTML does not cover the current 200-page sample')
    for case in dataset['cases']:
        if hashlib.sha256(source_html[case['id']].encode('utf-8')).hexdigest() != case['input_html_sha256']:
            raise ValueError('Frozen source HTML hash mismatch: ' + case['id'])
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.webmainbench-report-build-', dir=output.parent))
    try:
        shutil.copytree(source / 'artifacts', staging / 'artifacts')
        shutil.copytree(source / 'references', staging / 'references')
        (staging / 'sources').mkdir()
        for case_id, raw_html in source_html.items():
            (staging / 'sources' / f'{case_id}.html.txt').write_text(raw_html, encoding='utf-8')
        shutil.copytree(source / 'fixture-certificates', staging / 'fixture-certificates')
        shutil.copy2(source / 'sample-manifest.json', staging / 'sample-manifest.json')
        for name in ('dataset.json', 'results.jsonl', 'score-summaries.json', 'execution.json',
                     'physical-attempts.jsonl'):
            shutil.copy2(source / name, staging / name)
        if (source / 'latency-corrections.json').is_file():
            for name in ('latency-corrections.json', 'prior-latency-results.jsonl'):
                shutil.copy2(contained_path(source, name), staging / name)
        if review_path.is_file():
            shutil.copy2(review_path, staging / 'result-review.json')
        shutil.copy2(research_path, staging / 'public-benchmark-research.json')
        (staging / 'adoption').mkdir(exist_ok=True)
        shutil.copy2(adoption_path, staging / 'adoption' / 'evidence.json')
        shutil.copy2(corrections_path, staging / 'webmainbench-corrections.json')
        shutil.copy2(anomaly_review_path, staging / 'webmainbench-anomaly-review.json')
        (staging / 'report-changes.json').write_text(
            json.dumps({'schema_version': 2, 'releases': release_history}, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
        if fixture_correction is not None:
            shutil.copy2(fixture_correction_path, staging / 'fixture-correction.json')
        if teds_revision is not None:
            for name in ('teds-correction.json', 'teds-correction-review.json', 'original-results.jsonl'):
                shutil.copy2(contained_path(source, name), staging / name)
            for name in teds_revision[0]['source_code_sha256']:
                target = contained_path(staging / 'correction-source', name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(contained_path(source / 'correction-source', name), target)
        for row in records:
            artifact = contained_path(staging, row['artifact_path'])
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != row['output_sha256']:
                raise ValueError('WebMainBench output integrity failure: ' + row['attempt_id'])
        if latency_diagnostic is not None:
            (staging / 'latency-diagnostic.json').write_text(
                json.dumps(latency_diagnostic, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        generate_webmainbench(staging, dataset, records, summaries, manifests, review, research, corrections, adoption, fixture_correction, teds_revision, anomaly_review, latency_diagnostic, include_english=include_english)
        (staging / '.generated-report').write_text('Generated from persisted evidence. Rebuild; do not edit.\n')
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix='.report-swap-', dir=output.parent)) / 'report'
            output.rename(backup)
            try:
                staging.rename(output)
            except BaseException:
                backup.rename(output)
                raise
            shutil.rmtree(backup.parent)
        else:
            staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output / 'index.html'


def build_webmainbench_full_report(source: Path, output: Path) -> Path:
    """Build the separately bound complete 545-case report from certified evidence."""
    from scripts.run_certified_webmainbench_full import digest, read_jsonl
    from .webmainbench_diagnostics import validate_teds_revision
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Symlinked report evidence is forbidden')
    profile_path = Path(__file__).resolve().parents[2] / 'evaluation/webmainbench-full/current-report.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    certificate = json.loads((source / 'cohort-certificate.json').read_text(encoding='utf-8'))
    scoring_certificate = json.loads((source / 'scoring-certificate.json').read_text(encoding='utf-8'))
    if (certificate.get('kind') != 'certified_webmainbench_545_full_v1'
            or certificate.get('profile_sha256') != digest(profile_path)
            or certificate.get('preflight') != certificate.get('postflight')
            or json.loads((source / 'current-report.json').read_text(encoding='utf-8')) != profile):
        raise ValueError('Full-cohort certificate or profile mismatch')
    if (scoring_certificate.get('kind') != 'certified_webmainbench_scoring_v1'
            or scoring_certificate.get('cohort_certificate_sha256') != digest(source / 'cohort-certificate.json')
            or scoring_certificate.get('source_binding') != certificate['postflight']):
        raise ValueError('Complete scoring certificate mismatch')
    dataset = json.loads((source / 'dataset.json').read_text(encoding='utf-8'))
    records = read_jsonl(source / 'results.jsonl')
    six_binding = json.loads((source / 'six-method-binding.json').read_text(encoding='utf-8'))
    fixed_profile_path = Path(__file__).resolve().parents[2] / 'evaluation/webmainbench-full/moli-fixed-report.json'
    if (six_binding.get('kind') != 'webmainbench_six_version_full_scope_binding'
            or six_binding.get('baseline_certificate_sha256') != digest(source / 'cohort-certificate.json')
            or six_binding.get('fixed_profile_sha256') != digest(fixed_profile_path)
            or six_binding.get('fixed_certificate_sha256') != digest(source / 'fixed-cohort-certificate.json')
            or six_binding.get('fixed_scoring_certificate_sha256') != digest(source / 'fixed-scoring-certificate.json')
            or six_binding.get('results_sha256') != digest(source / 'original-results.jsonl')):
        raise ValueError('Six-version report evidence binding mismatch')
    fixed_cert = json.loads((source / 'fixed-cohort-certificate.json').read_text(encoding='utf-8'))
    fixed_profile = json.loads(fixed_profile_path.read_text(encoding='utf-8'))
    if (fixed_cert.get('preflight') != fixed_cert.get('postflight')
            or fixed_cert.get('profile_sha256') != digest(fixed_profile_path)
            or fixed_cert.get('binary_source_commit') != fixed_profile['source_commit']
            or fixed_cert.get('measured_binary_sha256') != fixed_profile['binary_sha256']
            or six_binding.get('fixed_source_commit') != fixed_profile['source_commit']):
        raise ValueError('Fixed Moli cohort was not certified')
    providers = profile['providers'] + ['moli-fixed']
    display_profile = {**profile, 'providers': providers,
                       'versions': {**profile['versions'], 'moli-fixed': '1.1.9'}}
    cases = {case['id']: case for case in dataset['cases']}
    if (len(cases) != 545 or len(records) != 545 * len(providers)
            or {(row['provider'], row['case_id']) for row in records} !=
            {(provider, case_id) for provider in providers for case_id in cases}):
        raise ValueError('Full WebMainBench result matrix mismatch')
    validate_teds_revision(source)
    content_binding_path = Path(__file__).resolve().parents[2] / 'evaluation/webmainbench-full/content-binding.json'
    content_binding = json.loads(content_binding_path.read_text(encoding='utf-8'))
    content_path = data_home() / 'report-assets/webmainbench-full' / content_binding['relative_path']
    if digest(content_path) != content_binding['sha256']:
        raise ValueError('Report prose differs from the compact content binding')
    failure_binding_path = Path(__file__).resolve().parents[2] / 'evaluation/webmainbench-full/failure-analysis-binding.json'
    failure_binding = json.loads(failure_binding_path.read_text(encoding='utf-8'))
    failure_path = data_home() / 'report-assets/webmainbench-full' / failure_binding['relative_path']
    if digest(failure_path) != failure_binding['sha256']:
        raise ValueError('Failure analysis differs from the compact review binding')
    failure_analysis = json.loads(failure_path.read_text(encoding='utf-8'))
    correction_binding_path = profile_path.parent / 'case-correction-binding.json'
    correction_root = None
    corrected_keys = set()
    if correction_binding_path.exists():
        from .webmainbench_correction import validate as validate_case_correction
        correction_binding = json.loads(correction_binding_path.read_text())
        correction_root = data_home() / correction_binding['relative_path']
        if digest(correction_root / 'certificate.json') != correction_binding['sha256']:
            raise ValueError('Case correction differs from bound certificate')
        _, corrected_rows, _ = validate_case_correction(correction_root, records, digest(source / 'results.jsonl'))
        corrected_keys = {(r['provider'], r['case_id']) for r in corrected_rows}
    failures = {(row['provider'], row['case_id']) for row in records
                if webmainbench_full_machine_issue(source, row)} - corrected_keys
    reviewed = {(row['provider'], row['case_id']) for row in failure_analysis['reviewed_failures']}
    if (failure_analysis.get('kind') != 'webmainbench_full_failure_analysis_v1'
            or failure_analysis.get('cohort_certificate_sha256') != digest(source / 'cohort-certificate.json')
            or reviewed != {item for item in failures if item[0] != 'moli-fixed'}
            or len(reviewed) != len(failure_analysis['reviewed_failures'])
            or any(not all(isinstance(row.get(key), str) and row[key].strip()
                           for key in ('category_label', 'cause', 'treatment', 'status'))
                   for row in failure_analysis['reviewed_failures'])):
        raise ValueError('Failure analysis does not cover every measured request failure')
    for case_id, case in cases.items():
        if digest(contained_path(source, f'references/{case_id}.md')) != case['reference_sha256']:
            raise ValueError('Reference changed: ' + case_id)
        if digest(contained_path(source, f'sources/{case_id}.html.txt')) != case['input_html_sha256']:
            raise ValueError('Frozen page changed: ' + case_id)
    for row in records:
        if digest(contained_path(source, row['artifact_path'])) != row['output_sha256']:
            raise ValueError('Output changed: ' + row['attempt_id'])
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.webmainbench-full-build-', dir=output.parent))
    try:
        for name in ('artifacts', 'references', 'sources', 'fixture-certificates', 'correction-source'):
            shutil.copytree(source / name, staging / name)
        for name in ('dataset.json', 'results.jsonl', 'original-results.jsonl',
                     'physical-attempts.jsonl', 'execution.json', 'score-summaries.json',
                     'cohort-certificate.json', 'current-report.json',
                     'scoring-certificate.json',
                     'teds-correction.json', 'teds-correction-review.json',
                     'six-method-binding.json', 'fixed-cohort-certificate.json',
                     'fixed-scoring-certificate.json', 'moli-fixed-report.json'):
            shutil.copy2(source / name, staging / name)
        shutil.copy2(content_path, staging / 'report-content.json')
        shutil.copy2(failure_path, staging / 'failure-analysis.json')
        shutil.copy2(Path(__file__).resolve().parents[2] / 'research/provider-adoption.json', staging / 'provider-adoption.json')
        summaries = json.loads((source / 'score-summaries.json').read_text(encoding='utf-8'))
        if correction_root:
            from .webmainbench_correction import apply as apply_case_correction
            records, summaries = apply_case_correction(correction_root, staging, records, summaries)
        illustration_binding = profile_path.parent / 'illustrations.json'
        if illustration_binding.exists():
            (staging / 'assets').mkdir(exist_ok=True)
            for asset in json.loads(illustration_binding.read_text())['assets']:
                asset_source = data_home() / 'report-assets/webmainbench-full' / asset['name']
                if digest(asset_source) != asset['sha256']:
                    raise ValueError('Report illustration changed: ' + asset['name'])
                shutil.copy2(asset_source, staging / 'assets' / asset['name'])
        generate_webmainbench_full(staging, dataset, records,
                                   summaries, display_profile)
        from scripts.check_webmainbench_full_report import check as check_webmainbench_full
        check_webmainbench_full(staging)
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix='.report-swap-', dir=output.parent)) / 'report'
            output.rename(backup)
            try:
                staging.rename(output)
            except BaseException:
                backup.rename(output)
                raise
            shutil.rmtree(backup.parent)
        else:
            staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output / 'index.html'


def build_wcxb_report(source: Path, corpus: Path, sample_path: Path, profile: dict, output: Path) -> Path:
    """Build the one current report from a fully certified WCXB matrix."""
    from scripts.run_webmainbench_benchmark import WCXB_PROVIDERS, load_wcxb_source
    from scripts.select_wcxb_sample import validate_manifest
    source, output = source.resolve(), output.resolve()
    certificate = json.loads((source / 'certification.json').read_text())
    summary = json.loads((source / 'wcxb-score-summary.json').read_text())
    records, corpus_hash = load_wcxb_source(corpus)
    sample_bytes = sample_path.read_bytes()
    sample_hash = hashlib.sha256(sample_bytes).hexdigest()
    sample = json.loads(sample_bytes)
    selected, distribution = validate_manifest(records, corpus_hash, sample)
    if (sample_hash != profile['sample_manifest_sha256']
            or sample_hash != certificate.get('sample_sha256_before')
            or sample_hash != certificate.get('sample_sha256_after')
            or sample.get('size') != profile['case_count']):
        raise ValueError('WCXB stratified sample binding failed')
    records = selected
    for record in records:
        safe_identifier(record['track_id'])
    if (not certificate.get('complete') or corpus_hash != certificate['source_sha256_before']
            or corpus_hash != certificate['source_sha256_after']
            or corpus_hash != summary['source_sha256']
            or corpus_hash != profile['source_dataset_sha256']):
        raise ValueError('WCXB source, certificate, summary and profile disagree')
    evaluator_hash = hashlib.sha256(contained_path(corpus, 'evaluate.py').read_bytes()).hexdigest()
    if evaluator_hash != profile['official_evaluator_sha256'] or summary.get('official_evaluator_sha256') != evaluator_hash:
        raise ValueError('WCXB official evaluator differs from the certified profile')
    if [x['provider'] for x in certificate['providers']] != list(WCXB_PROVIDERS) or profile['providers'] != list(WCXB_PROVIDERS):
        raise ValueError('WCXB participant registry differs from the certificate')
    candidate = next(x for x in certificate['providers'] if x['provider'] == 'moli-pr643')
    if candidate['identity'].get('binary_sha256') != profile['candidate_binary_sha256']:
        raise ValueError('WCXB candidate binary differs from the current report profile')
    released = next(x for x in certificate['providers'] if x['provider'] == 'moli')
    if released['identity'].get('binary_sha256') != profile['release_binary_sha256']:
        raise ValueError('WCXB released Moli binary differs from the current report profile')
    rows = [json.loads(line) for line in (source / 'wcxb-scores.jsonl').read_text().splitlines() if line]
    case_ids = {r['track_id'] for r in records}
    correction_root = source / 'latency-correction'
    correction_receipt = correction_root / 'receipt.json'
    if (not correction_receipt.is_file()
            or hashlib.sha256(correction_receipt.read_bytes()).hexdigest()
            != profile.get('latency_correction_receipt_sha256')):
        raise ValueError('WCXB full-scope latency correction is missing or differs from profile')
    _, corrected_calls = load_full_paired_latency(correction_root, profile, case_ids)
    correction_certificate = json.loads((correction_root / 'certification.json').read_text())
    if (correction_certificate.get('kind') != 'certified_wcxb_paired_latency'
            or correction_certificate.get('complete') is not True
            or correction_certificate.get('receipt_sha256')
            != hashlib.sha256(correction_receipt.read_bytes()).hexdigest()):
        raise ValueError('WCXB corrected timing is not certified')
    if any(
        abs(row['total_elapsed_ms'] - corrected_calls[row['case_id'],
            'release' if row['provider'] == 'moli' else 'pr']['elapsed_ms']) > .01
        for row in rows if row['provider'] in ('moli', 'moli-pr643')
    ):
        raise ValueError('WCXB reported Moli latency differs from the selected paired round')
    optional_root = source / 'optional-ui-4017'
    optional_receipt = optional_root / 'receipt.json'
    if (not optional_receipt.is_file() or optional_receipt.is_symlink()
            or hashlib.sha256(optional_receipt.read_bytes()).hexdigest()
            != profile.get('optional_ui_receipt_sha256')):
        raise ValueError('WCXB optional UI-stripping evidence is missing or differs from profile')
    optional = json.loads(optional_receipt.read_text())
    if (optional.get('case_id') != '4017' or optional.get('source_sha256') != corpus_hash
            or optional.get('builds', {}).get('release', {}).get('binary_sha256') != profile['release_binary_sha256']
            or optional.get('builds', {}).get('candidate', {}).get('binary_sha256') != profile['candidate_binary_sha256']
            or optional['builds']['release']['description_present']
            or not optional['builds']['candidate']['description_present']):
        raise ValueError('WCXB optional UI-stripping comparison does not bind both builds')
    for name in ('release', 'candidate'):
        for ext, key in (('md', 'output_sha256'), ('stderr', 'stderr_sha256')):
            artifact = optional_root / f'{name}.{ext}'
            if (not artifact.is_file() or artifact.is_symlink()
                    or hashlib.sha256(artifact.read_bytes()).hexdigest() != optional['builds'][name][key]):
                raise ValueError('WCXB optional UI-stripping artifact changed: ' + artifact.name)
    if len(rows) != len(records) * len(WCXB_PROVIDERS) or {(r['provider'], r['case_id']) for r in rows} != {(p,c) for p in WCXB_PROVIDERS for c in case_ids}:
        raise ValueError('WCXB score matrix is incomplete')
    if output.exists() and not (output / '.generated-report').is_file():
        raise ValueError('Refusing to replace an unmarked report')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.wcxb-report-build-', dir=output.parent))
    try:
        for name in ('certification.json', 'wcxb-score-summary.json', 'wcxb-scores.jsonl'):
            shutil.copy2(source / name, staging / name)
        shutil.copy2(sample_path, staging / 'sample-manifest.json')
        (staging / 'research').mkdir()
        (staging / 'research/wcxb-full-paired-latency').mkdir()
        for name in ('receipt.json', 'certification.json', 'calls.jsonl'):
            shutil.copy2(correction_root / name, staging / 'research/wcxb-full-paired-latency' / name)
        (staging / 'research/wcxb-optional-ui-4017').mkdir()
        for name in ('receipt.json', 'release.md', 'candidate.md', 'release.stderr', 'candidate.stderr'):
            shutil.copy2(optional_root / name, staging / 'research/wcxb-optional-ui-4017' / name)
        shutil.copy2(Path(__file__).resolve().parents[2] / 'research/report-changes.json',
                     staging / 'research/report-changes.json')
        shutil.copy2(Path(__file__).resolve().parents[2] / 'research/provider-adoption.json',
                     staging / 'research/provider-adoption.json')
        copy_bound_assets(source / 'report-assets', staging, profile['report_assets'])
        (staging / 'references').mkdir()
        (staging / 'inputs').mkdir()
        for case in records:
            (staging / 'references' / f'{case["track_id"]}.txt').write_text(case['groundtruth_content'], encoding='utf-8')
            (staging / 'inputs' / f'{case["track_id"]}.txt').write_text(case['html'], encoding='utf-8')
        identities = {}
        for batch in certificate['providers']:
            provider = batch['provider']
            run_dir = source / provider
            results_file = run_dir / 'results.jsonl'
            if hashlib.sha256(results_file.read_bytes()).hexdigest() != batch['results_sha256']:
                raise ValueError('WCXB results changed: ' + provider)
            if hashlib.sha256((run_dir / 'physical-attempts.jsonl').read_bytes()).hexdigest() != summary['providers'][provider]['physical_attempts_sha256']:
                raise ValueError('WCXB physical attempts changed: ' + provider)
            manifest = json.loads((run_dir / 'execution.json').read_text())
            if manifest['run_id'] != batch['run_id'] or manifest['source_dataset_sha256'] != corpus_hash:
                raise ValueError('WCXB run identity changed: ' + provider)
            identities[provider] = manifest['implementation_identity']
            raw_rows = [json.loads(line) for line in results_file.read_text().splitlines() if line]
            if len(raw_rows) != len(records):
                raise ValueError('WCXB provider results incomplete: ' + provider)
            for row in raw_rows:
                artifact = contained_path(run_dir, row['artifact_path'])
                if artifact.is_symlink() or hashlib.sha256(artifact.read_bytes()).hexdigest() != row['output_sha256']:
                    raise ValueError('WCXB output changed: ' + row['attempt_id'])
                target = staging / 'artifacts' / provider / f'{row["track_id"]}.txt'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact, target)
            for scored in (r for r in rows if r['provider'] == provider):
                for retry in scored.get('retry_failures', []):
                    artifact = contained_path(source, retry['artifact_path'])
                    if artifact.is_symlink() or hashlib.sha256(artifact.read_bytes()).hexdigest() != retry['output_sha256']:
                        raise ValueError('WCXB retained retry artifact changed: ' + scored['attempt_id'])
                    target = contained_path(staging, retry['artifact_path'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(artifact, target)
        ground_truth = {}
        for record in records:
            gt_path = contained_path(corpus / 'test' / 'ground-truth', f'{record["track_id"]}.json')
            gt = json.loads(gt_path.read_text())['ground_truth']
            ground_truth[record['track_id']] = {
                'main_content': record['groundtruth_content'],
                'page_type': record['meta']['page_type'],
                'with': gt.get('with') or [], 'without': gt.get('without') or [],
            }
        generate_wcxb(staging, rows, summary, ground_truth, identities, profile)
        evidence_root = staging / 'evidence'
        evidence_root.mkdir()
        evidence_hashes = {}
        rows_by_case = {}
        for row in rows:
            rows_by_case.setdefault(row['case_id'], {})[row['provider']] = row
        for record in records:
            case_id = record['track_id']
            outputs = {}
            retries = {}
            for provider in WCXB_PROVIDERS:
                outputs[provider] = (staging / 'artifacts' / provider / f'{case_id}.txt').read_text(encoding='utf-8')
                retries[provider] = [
                    {**retry, 'output': contained_path(staging, retry['artifact_path']).read_text(encoding='utf-8')}
                    for retry in rows_by_case[case_id][provider].get('retry_failures', [])
                ]
            payload = {
                'schema_version': 1, 'case_id': case_id,
                'source_html': record['html'], 'reference': record['groundtruth_content'],
                'outputs': outputs, 'retry_failures': retries,
            }
            target = evidence_root / f'{case_id}.json'
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            evidence_hashes[case_id] = hashlib.sha256(target.read_bytes()).hexdigest()
        review_file = staging / 'research/wcxb-representative-review.json'
        if review_file.is_file():
            review = json.loads(review_file.read_text())
            if (review.get('scope') != 'targeted_representative_anomaly_review'
                    or review.get('total_results') != len(rows)
                    or review.get('result_count') != sum(len(case['outputs']) for case in review['cases'])
                    or review.get('unreviewed_results') != len(rows) - review['result_count']):
                raise ValueError('WCXB representative review scope differs from measured matrix')
            for case_review in review['cases']:
                case_id = case_review['case_id']
                if case_id not in rows_by_case or not set(case_review['outputs']) <= set(WCXB_PROVIDERS):
                    raise ValueError('WCXB review case or method inventory differs')
                evidence = json.loads((evidence_root / f'{case_id}.json').read_text())
                if (hashlib.sha256(evidence['source_html'].encode()).hexdigest() != case_review['source_html_sha256']
                        or hashlib.sha256(evidence['reference'].encode()).hexdigest() != case_review['reference_sha256']):
                    raise ValueError('WCXB reviewed input or reference changed: ' + case_id)
                for provider in case_review['outputs']:
                    bound = case_review['outputs'][provider]
                    if (bound['sha256'] != rows_by_case[case_id][provider]['output_sha256']
                            or bound['attempt_id'] != rows_by_case[case_id][provider]['attempt_id']):
                        raise ValueError('WCXB reviewed result changed: ' + case_id + '/' + provider)
        (staging / 'evidence-manifest.json').write_text(json.dumps(evidence_hashes, sort_keys=True, indent=2) + '\n')
        for folder in ('artifacts', 'references', 'inputs'):
            shutil.rmtree(staging / folder, ignore_errors=True)
        (staging / '.generated-report').write_text('generated\n')
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix='.wcxb-report-old-', dir=output.parent)) / 'report'
            output.rename(backup)
            try: staging.rename(output)
            except BaseException:
                backup.rename(output)
                raise
            shutil.rmtree(backup.parent)
        else:
            staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output / 'index.html'
