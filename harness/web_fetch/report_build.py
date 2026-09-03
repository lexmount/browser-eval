"""Build the single current report from an explicit evidence directory."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from .report import generate, succeeded, measured, failure_category, TRACKS
from .failure_diagnosis import diagnose
from .safety import contained_path, validate_report_ids
from .reference_lifecycle import require_verified_reference_checks


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


def build_report(source: Path, output: Path) -> Path:
    source, output = source.resolve(), output.resolve()
    if source == output or output in source.parents:
        raise ValueError('Report output must not contain its evidence source')
    marker = '.generated-report'
    if output.exists() and not (output/marker).is_file():
        raise ValueError(f'Refusing to replace an unmarked directory: {output}')
    dataset = json.loads(contained_path(source,'dataset.json').read_text())
    records = [json.loads(line) for line in contained_path(source,'results.jsonl').read_text().splitlines()]
    validate_report_ids(dataset,records)
    cases = {c['id']:c for c in dataset['cases']}
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
    require_verified_reference_checks(manifests, cases=dataset, records=records, source=source)
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
        (staging/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
        copy_methodology_assets(staging)
        if (source/'scoring-revisions.json').is_file():
            shutil.copy2(source/'scoring-revisions.json', staging/'scoring-revisions.json')
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
