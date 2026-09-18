"""Build the single current report from an explicit evidence directory."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from .report import generate, generate_public_benchmark_research, generate_webmainbench, generate_wcxb, succeeded, measured, failure_category, TRACKS
from .failure_diagnosis import diagnose
from .safety import contained_path, safe_identifier, validate_report_ids
from .reference_lifecycle import require_verified_reference_checks
from .reliability import assess_reliability
from .storage import require_selection_history,data_home
from .wcxb_latency import load_paired_latency
from .scoring import SCORING_SOURCE_SHA256
from .webmainbench_diagnostics import validate_teds_revision_if_present
from .artifacts import (
    record_public_call_log_bindings, verify_record_private_artifacts,
    verify_public_call_log, verify_record_public_call_logs,
)


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


def build_public_benchmark_research(source: Path, output: Path) -> Path:
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
        generate_public_benchmark_research(staging, research)
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


def build_report(source: Path, output: Path) -> Path:
    source, output = source.resolve(), output.resolve()
    if source == output or output in source.parents:
        raise ValueError('Report output must not contain its evidence source')
    marker = '.generated-report'
    if output.exists() and not (output/marker).is_file():
        raise ValueError(f'Refusing to replace an unmarked directory: {output}')
    dataset_probe = json.loads(contained_path(source, 'dataset.json').read_text())
    if dataset_probe.get('comparison', {}).get('kind') == 'webmainbench_frozen_html_to_markdown':
        return build_webmainbench_report(source, output)
    require_selection_history(source)
    dataset = json.loads(contained_path(source,'dataset.json').read_text())
    records = [json.loads(line) for line in contained_path(source,'results.jsonl').read_text().splitlines()]
    validate_report_ids(dataset,records)
    cases = {c['id']:c for c in dataset['cases']}
    from .result_review import require_result_review
    result_review = require_result_review(source, dataset, records)
    speed_root=source/'speed-cohort'
    if speed_root.exists():
        speed=json.loads((speed_root/'speed-comparison.json').read_text())
        if speed.get('bindings',{}).get('dataset_sha256') != hashlib.sha256((speed_root/'dataset.json').read_bytes()).hexdigest():
            raise ValueError('Speed comparison dataset binding mismatch')
        if speed.get('bindings',{}).get('results_sha256') != hashlib.sha256((speed_root/'results.jsonl').read_bytes()).hexdigest():
            raise ValueError('Speed comparison result binding mismatch')
        speed_records=[json.loads(line) for line in (speed_root/'results.jsonl').read_text().splitlines()]
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
            shutil.copytree(speed_root,staging/'speed-cohort',ignore=shutil.ignore_patterns(
                'result-review.json','review-history','private_artifacts'))
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
        reliability=assess_reliability(dataset,records)
        (staging/'reliability.json').write_text(json.dumps(reliability,ensure_ascii=False,indent=2)+'\n')
        # Keep the downloadable result file byte-identical to the independently
        # reviewed evidence. Derived diagnosis/title fields remain report-only.
        (staging/'results.jsonl').write_bytes(contained_path(source,'results.jsonl').read_bytes())
        if result_review is not None:
            (staging/'result-review.json').write_text(json.dumps(result_review,ensure_ascii=False,indent=2)+'\n')
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
        generate(staging,dataset,records,manifests)
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
        'results_jsonl_sha256': 'results.jsonl',
        'score_summaries_json_sha256': 'score-summaries.json',
    }
    for binding, filename in expected_files.items():
        actual = hashlib.sha256(contained_path(source, filename).read_bytes()).hexdigest()
        if bindings.get(binding) != actual:
            raise ValueError(f'WebMainBench anomaly review {binding} mismatch')
    if (bindings.get('source_dataset_sha256') != dataset.get('source_dataset_sha256')
            or bindings.get('sample_manifest_sha256') != dataset.get('sample_manifest_sha256')):
        raise ValueError('WebMainBench anomaly review dataset binding mismatch')
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


def build_webmainbench_report(source: Path, output: Path) -> Path:
    """Build the current public-benchmark report without legacy task predicates."""
    dataset = json.loads(contained_path(source, 'dataset.json').read_text())
    records = [json.loads(line) for line in contained_path(source, 'results.jsonl').read_text().splitlines() if line.strip()]
    teds_revision = validate_teds_revision_if_present(source, records)
    summaries = json.loads(contained_path(source, 'score-summaries.json').read_text())
    manifests = json.loads(contained_path(source, 'execution.json').read_text())['batches']
    review_path = source / 'result-review.json'
    review = json.loads(review_path.read_text()) if review_path.is_file() else None
    research_path = Path(__file__).resolve().parents[2] / 'research/public-web-benchmarks.json'
    research = json.loads(research_path.read_text(encoding='utf-8'))
    adoption_path = Path(__file__).resolve().parents[2] / 'research/provider-adoption.json'
    adoption = json.loads(adoption_path.read_text(encoding='utf-8'))
    corrections_path = Path(__file__).resolve().parents[2] / 'research/webmainbench-corrections.json'
    corrections = json.loads(corrections_path.read_text(encoding='utf-8'))
    anomaly_review_path = Path(__file__).resolve().parents[2] / 'research/webmainbench-anomaly-review.json'
    anomaly_review = json.loads(anomaly_review_path.read_text(encoding='utf-8'))
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
        if review_path.is_file():
            shutil.copy2(review_path, staging / 'result-review.json')
        shutil.copy2(research_path, staging / 'public-benchmark-research.json')
        (staging / 'adoption').mkdir(exist_ok=True)
        shutil.copy2(adoption_path, staging / 'adoption' / 'evidence.json')
        shutil.copy2(corrections_path, staging / 'webmainbench-corrections.json')
        shutil.copy2(anomaly_review_path, staging / 'webmainbench-anomaly-review.json')
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
        generate_webmainbench(staging, dataset, records, summaries, manifests, review, research, corrections, adoption, fixture_correction, teds_revision, anomaly_review)
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
    diagnostic_path = data_home() / 'evidence/current/wcxb-pr643-paired-latency.json'
    load_paired_latency(diagnostic_path, profile, case_ids)
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
        shutil.copy2(diagnostic_path, staging / 'research/wcxb-paired-latency.json')
        shutil.copy2(Path(__file__).resolve().parents[2] / 'research/report-changes.json',
                     staging / 'research/report-changes.json')
        shutil.copy2(Path(__file__).resolve().parents[2] / 'research/provider-adoption.json',
                     staging / 'research/provider-adoption.json')
        review_source = Path(__file__).resolve().parents[2] / 'research/wcxb-representative-review.json'
        if review_source.is_file():
            shutil.copy2(review_source, staging / 'research/wcxb-representative-review.json')
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
