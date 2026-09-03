"""Append a complete independently measured dynamic cohort; never replace attempts."""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.render_evidence import validate_render_reference
from harness.web_fetch.safety import contained_path, validate_report_ids


def frozen_case(case):
    """Bind results to identity, scope and scoring, not later attestations."""
    fields = (
        'id', 'name', 'url', 'category', 'track', 'task_kind', 'task_prompt',
        'expected_any', 'required_facts', 'forbidden_any', 'min_content_chars',
        'fact_coverage_threshold', 'ground_truth_main_content', 'reference_answer',
        'expected_resources', 'content_score_applicable', 'required_patterns',
    )
    frozen = {key: case.get(key) for key in fields}
    frozen['source_benchmark'] = {
        key: value for key, value in case.get('source_benchmark', {}).items()
        if key != 'render_evidence'
    }
    return frozen


def cases_by_id(dataset):
    cases = {case['id']: case for case in dataset['cases']}
    if not cases or len(cases) != len(dataset['cases']):
        raise ValueError('Empty or duplicate supplement cases')
    return cases


def append(source, reference, measured, checks):
    source, reference, measured = map(Path, (source, reference, measured))
    # copytree follows links by default; reject them before any evidence reads.
    for root in (source, reference, measured):
        if root.is_symlink() or any(child.is_symlink() for child in root.rglob('*')):
            raise ValueError('Evidence trees must not contain symlinks')
    dataset = json.loads((source/'dataset.json').read_text())
    supplement = json.loads((reference/'dataset.json').read_text())
    records = [json.loads(s) for s in (measured/'results.jsonl').read_text().splitlines()]
    previous_text = (source/'results.jsonl').read_text()
    previous = [json.loads(s) for s in previous_text.splitlines()]
    validate_report_ids(supplement, records)
    reference_cases = cases_by_id(supplement)
    new_ids = set(reference_cases)
    if new_ids & {c['id'] for c in dataset['cases']}:
        raise ValueError('Supplement already present; refusing replacement')
    measured_cases = cases_by_id(json.loads((measured/'dataset.json').read_text()))
    if ({key: frozen_case(case) for key, case in reference_cases.items()} !=
            {key: frozen_case(case) for key, case in measured_cases.items()}):
        raise ValueError('Measured dataset differs from frozen reference identity or scoring')
    execution = json.loads((source/'execution.json').read_text())
    if not isinstance(execution.get('batches'), list):
        raise ValueError('Invalid execution batches')
    manifest = json.loads((measured/'manifest.json').read_text())
    providers = {p['name'] for p in manifest['providers']}
    if not providers or len(providers) != len(manifest['providers']):
        raise ValueError('Empty or duplicate supplement providers')
    if len(records) != len(new_ids)*len(providers) or {(r['case_id'],r['provider']) for r in records} != {(c,p) for c in new_ids for p in providers}:
        raise ValueError('Incomplete or duplicate supplement attempts')
    attempt_ids = [r['attempt_id'] for r in records]
    if (len(set(attempt_ids)) != len(attempt_ids)
            or set(attempt_ids) & {r['attempt_id'] for r in previous}
            or sorted(manifest['attempt_ids']) != sorted(attempt_ids)
            or manifest['attempt_count'] != len(records)
            or manifest['dataset']['case_count'] != len(new_ids)):
        raise ValueError('Supplement attempt identity or manifest mismatch')
    audits = {}
    source_hashes = {}
    for c in supplement['cases']:
        check = next(x for x in checks if x['id']==c['id'])
        evidence_dir = contained_path(reference, f'sources/{c["id"]}')
        evidence_files = {p.relative_to(evidence_dir): p.read_bytes()
                          for p in evidence_dir.rglob('*') if p.is_file()}
        rendered = evidence_files[Path('rendered.txt')].decode('utf-8')
        if '\n\n'.join(check['entries']) != rendered or not check['satisfied']:
            raise ValueError('Completion recheck differs from pre-run frozen evidence')
        c['source_benchmark']['render_evidence'] = {
            'scope':c['source_benchmark']['capture_scope'], 'rendered_truncated':False,
            'completion':{'condition':f"{check['id']}: {len(check['entries'])} nonempty target records with populated text fields; no interaction",
                'timeout_ms':check['timeout_ms'], 'elapsed_ms':check['elapsed_ms'], 'satisfied':True,
                'checked_at':check['checked_at'], 'phase':'post-run independent reproduction of pre-run frozen reference'}}
        audit = validate_render_reference(c,evidence_files[Path('initial.html.txt')],rendered)
        if not audit['valid']:raise ValueError(audit['errors'])
        evidence = json.loads(evidence_files[Path('evidence.json')])
        for stored, actual in (
            ('initial_sha256', 'initial_html_sha256'),
            ('rendered_sha256', 'rendered_text_sha256'),
            ('reference_sha256', 'reference_sha256'),
        ):
            if evidence[stored] != audit[actual]:
                raise ValueError(f'Source evidence hash mismatch: {c["id"]}/{stored}')
        if (evidence_files[Path('reference.txt')] != c['ground_truth_main_content'].encode()
                or c['source_verification']['reference_sha256'] != audit['reference_sha256']):
            raise ValueError(f'Frozen reference hash mismatch: {c["id"]}')
        audits[c['id']] = audit
        source_hashes[c['id']] = {path: hashlib.sha256(raw).hexdigest()
                                 for path, raw in evidence_files.items()}
    artifact_paths = [contained_path(source, r['artifact_path']) for r in records]
    if len(set(artifact_paths)) != len(artifact_paths):
        raise ValueError('Duplicate supplement artifact paths')
    for r in records:
        if Path(r['artifact_path']).parts[0] != 'artifacts':
            raise ValueError('Output paths must be inside artifacts/')
        raw = contained_path(measured, r['artifact_path'])
        if hashlib.sha256(raw.read_bytes()).hexdigest()!=r['metrics']['content_sha256']:raise ValueError('Output hash mismatch')
        if contained_path(source, r['artifact_path']).exists():raise ValueError('Output collision')
    for c in supplement['cases']:
        target = source/'sources'/c['id']
        if target.exists():
            for relative, expected_hash in source_hashes[c['id']].items():
                original = contained_path(target, relative)
                if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=expected_hash:
                    raise ValueError('Source evidence collision')
    dataset['cases'] += supplement['cases']
    baseline_names=sorted({r['provider'] for r in previous})
    dataset['coverage_plan']={track:(sorted(providers) if track=='dynamic' else baseline_names) for track in {c.get('track','web') for c in dataset['cases']}}
    manifest['dataset']['source']='dynamic-content-reference/dataset.json'
    manifest['provider_config_source']='configs/providers.resources.json'
    execution['batches'].append(manifest)
    # Finish serialization and every input/collision check before staging or
    # touching canonical data. Scores are copied verbatim, never recomputed.
    outputs = {
        'dataset.json': json.dumps(dataset,ensure_ascii=False,indent=2)+'\n',
        'results.jsonl': previous_text + ('\n' if previous_text and not previous_text.endswith('\n') else '')
            + ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),
        'execution.json': json.dumps(execution,ensure_ascii=False,indent=2)+'\n',
    }
    transaction = Path(tempfile.mkdtemp(prefix=f'.{source.name}-append-', dir=source.parent))
    staged, backup = transaction/'staged', transaction/'original'
    committed = False
    try:
        shutil.copytree(source, staged)
        for c in supplement['cases']:
            target = staged/'sources'/c['id']
            if not target.exists():
                shutil.copytree(reference/'sources'/c['id'], target)
            for relative, expected_hash in source_hashes[c['id']].items():
                copied = contained_path(target, relative)
                if not copied.is_file() or hashlib.sha256(copied.read_bytes()).hexdigest()!=expected_hash:
                    raise ValueError(f'Staged source evidence hash mismatch: {c["id"]}/{relative}')
            (target/'render-audit.json').write_text(json.dumps(audits[c['id']],ensure_ascii=False,indent=2)+'\n')
        for r in records:
            target = contained_path(staged, r['artifact_path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(contained_path(measured, r['artifact_path']), target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != r['metrics']['content_sha256']:
                raise ValueError('Staged output hash mismatch')
        for name, content in outputs.items():
            (staged/name).write_text(content)
        source.rename(backup)
        try:
            staged.rename(source)
        except BaseException:
            try:
                backup.rename(source)
            except BaseException as recovery_error:
                raise RuntimeError(f'Rollback failed; original data retained at {backup}') from recovery_error
            raise
        committed = True
    finally:
        # Never delete the sole original if rollback itself failed.
        if committed or not backup.exists():
            shutil.rmtree(transaction, ignore_errors=True)
    print(f'Appended {len(new_ids)} cases, {len(records)} measured attempts; baseline records preserved')


if __name__=='__main__':
    append(ROOT/'evaluation',ROOT/'runs/dynamic-content-reference',ROOT/'runs/dynamic-content-measured',
           json.loads((ROOT/'research/dynamic-completion-checks.json').read_text()))
