"""Build the single current report from an explicit evidence directory."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from .report import generate, generate_public_benchmark_research, generate_webmainbench, succeeded, measured, failure_category, TRACKS
from .failure_diagnosis import diagnose
from .safety import contained_path, validate_report_ids
from .reference_lifecycle import require_verified_reference_checks
from .reliability import assess_reliability
from .storage import require_selection_history,data_home


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
    if dataset_probe.get('id') == 'webmainbench-545-preselected-200-five-browsers':
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
        require_result_review(speed_root, json.loads((speed_root/'dataset.json').read_text()), speed_records)
        for row in speed_records:
            raw=contained_path(speed_root,row['artifact_path'])
            if hashlib.sha256(raw.read_bytes()).hexdigest()!=row['metrics']['content_sha256']:
                raise ValueError('Speed cohort output integrity failure: '+row['attempt_id'])
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
        contract = cases[r['case_id']].get('acceptance_contract')
        if contract:
            from .stable_tasks import validate_acceptance_contract
            validate_acceptance_contract(contract)
            expected = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if r['metrics'].get('acceptance_contract_sha256') != expected:
                raise ValueError(f'Acceptance contract hash missing or mismatched: {r["attempt_id"]}')
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
            shutil.copytree(speed_root,staging/'speed-cohort')
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
            shutil.copy2(source/'scoring-revisions.json', staging/'scoring-revisions.json')
        if (source/'reference-revisions.json').is_file():
            shutil.copy2(source/'reference-revisions.json', staging/'reference-revisions.json')
        if (source/'latency-corrections.json').is_file():
            shutil.copy2(source/'latency-corrections.json', staging/'latency-corrections.json')
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


def build_webmainbench_report(source: Path, output: Path) -> Path:
    """Build the current public-benchmark report without legacy task predicates."""
    dataset = json.loads(contained_path(source, 'dataset.json').read_text())
    records = [json.loads(line) for line in contained_path(source, 'results.jsonl').read_text().splitlines() if line.strip()]
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
    providers = dataset['coverage_plan']['static_html']
    case_ids = {case['id'] for case in dataset['cases']}
    if len(case_ids) != 200 or len(records) != 200 * 5:
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
        for row in records:
            artifact = contained_path(staging, row['artifact_path'])
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != row['output_sha256']:
                raise ValueError('WebMainBench output integrity failure: ' + row['attempt_id'])
        generate_webmainbench(staging, dataset, records, summaries, manifests, review, research, corrections, adoption)
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
