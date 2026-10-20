"""Build the single current report from an explicit evidence directory."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from harness.record_io import file_sha256, load_jsonl
from harness.reporting.assets import copy_bound_assets

from .artifacts import (
    copy_cohort_evidence,
    diagnostic_log_binding,
    materialize_reference_checks,
    materialize_task_references,
    record_public_call_log_bindings,
    verify_public_call_log,
    verify_record_private_artifacts,
    verify_record_public_call_logs,
)
from .failure_diagnosis import diagnose
from .reference_lifecycle import require_verified_reference_checks
from .reliability import assess_reliability
from .report import (
    TRACKS,
    failure_category,
    generate,
    generate_public_benchmark_research,
    generate_wcxb,
    generate_webmainbench,
    generate_webmainbench_full,
    measured,
    succeeded,
    webmainbench_full_machine_issue,
)
from .report_changes import webmainbench_history
from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.web_fetch.safety import reject_evidence_symlinks, validate_report_ids
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
from .storage import data_home, require_selection_history
from .wcxb_latency import load_full_paired_latency
from .webmainbench_diagnostics import validate_teds_revision_if_present
from .webmainbench_latency_correction import prior_results_path
from .webmainbench_latency_diagnostic import load_diagnostic as load_latency_diagnostic

REPOSITORY = Path(__file__).resolve().parents[2]


def _validate_stable_annotation_evidence(case, source, dataset):
    """Validate anchors against their primary or registered qualification evidence."""
    from copy import deepcopy

    from .reference_inputs import input_tree, read_dataset_bytes
    from lexbench_webfetch.reference_quality import bind_annotation_evidence

    checked = deepcopy(case)
    try:
        bind_annotation_evidence(checked, source)
        return checked['source_benchmark']['stable_annotation']['anchor_evidence_sha256']
    except ValueError as error:
        if not str(error).startswith('Stable text lacks independent evidence:'):
            raise
    identifier = case['id']
    relative = f'sources/{identifier}/mixed-session-stable-text.json'
    aggregate = contained_path(source, relative, reject_symlinks=True)
    if not aggregate.is_file():
        raise ValueError('Stable text lacks registered qualification evidence: ' + identifier)
    matches = []
    for declaration in dataset.get('comparison', {}).get('input_sources', []):
        if (not isinstance(declaration, dict)
                or set(declaration) != {'path', 'sha256', 'case_ids'}):
            continue
        path = contained_path(REPOSITORY, declaration['path'], reject_symlinks=True)
        raw = read_dataset_bytes(path)
        if hashlib.sha256(raw).hexdigest() != declaration['sha256']:
            raise ValueError('Registered input source changed: ' + declaration['path'])
        source_ids = {row['id'] for row in json.loads(raw).get('cases', [])}
        candidate = contained_path(input_tree(path, 'sources'),
                                   f'{identifier}/mixed-session-stable-text.json',
                                   reject_symlinks=True)
        if identifier in source_ids and candidate.is_file():
            matches.append(candidate)
    if not matches or any(candidate.read_bytes() != aggregate.read_bytes() for candidate in matches):
        raise ValueError('Stable text qualification evidence is not registry-bound: ' + identifier)
    annotation = checked['source_benchmark']['stable_annotation']
    primary_sha256 = annotation['anchor_evidence_sha256']
    annotation['anchor_evidence'] = relative
    annotation.pop('anchor_evidence_sha256', None)
    bind_annotation_evidence(checked, source)
    return primary_sha256


def build_diagnostic_cohorts(source: Path | None, output: Path, profile: Path | None = None) -> Path:
    """Build an explicitly requested diagnostic edition using the WebFetch owner."""
    from harness.reporting.templates import resolve_template

    from .report import generate_cohort_diagnostic
    from .report_diagnostic import (
        check_diagnostic_report,
        load_diagnostic,
        prepare_diagnostic_evidence,
    )

    if profile is not None:
        configuration = json.loads(profile.read_text())
        binding = configuration.get('diagnostic_input')
        if binding:
            from .storage import data_home
            selected = source or contained_path(data_home(), binding['path'])
            if (not selected.is_file() or selected.is_symlink()
                    or file_sha256(selected) != binding['sha256']):
                raise ValueError('Diagnostic instance input differs from its profile binding')
            source = selected
    if source is None:
        raise ValueError('Diagnostic instance needs evidence or a bound diagnostic_input')
    source, output = source.resolve(), output.resolve()
    input_sha256 = file_sha256(source) if source.is_file() else None
    if source == output or source.is_relative_to(output) or output.is_relative_to(source) or output.exists():
        raise ValueError("Diagnostic build requires a new output outside its evidence")
    template = resolve_template("webfetch")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".diagnostic-report-", dir=output.parent))
    try:
        if source.is_file():
            prepare_diagnostic_evidence(source,staging)
            source=staging
        model, files = load_diagnostic(source)
        for relative, raw in files.items():
            target = contained_path(staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        model_bytes = (source / "report.json").read_bytes()
        (staging / "report.json").write_bytes(model_bytes)
        generate_cohort_diagnostic(staging, model)
        manifest = {"schema_version": 1, "template": template,
                    "evidence_state": "diagnostic", "certified": False,
                    "languages": ["zh"], "provider_calls_added": 0,
                    "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
                    "input_sha256": input_sha256,
                    "artifact_hashes": {path: hashlib.sha256(raw).hexdigest()
                                        for path, raw in files.items()}}
        (staging / "report-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        check_diagnostic_report(staging, profile=profile)
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging)
        raise
    return output / "index.html"


def require_risk_control_certification(source: Path, dataset: dict, records: list[dict]) -> None:
    """Validate the complete profile-bound risk-control cohort before reporting."""
    from .risk_control import validate_risk_control_certification

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



def build_public_benchmark_research(source: Path, output: Path, *, include_english: bool = False,
                                language: str | None = None) -> Path:
    """Build the research-only companion without measured evidence."""
    from harness.report_languages import includes_english, select_report_language
    include_english = includes_english(language, include_english)
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
        if language is not None:
            select_report_language(staging, language)
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
    return output / ('en/index.html' if language == 'en' else 'index.html')


def _install_report_tree(staging: Path, output: Path):
    if output.exists():
        backup = Path(tempfile.mkdtemp(prefix='.report-swap-', dir=output.parent)) / 'report'
        output.rename(backup)
        try:
            staging.rename(output)
        except BaseException:
            backup.rename(output)
            backup.parent.rmdir()
            raise
        shutil.rmtree(backup.parent)
    else:
        staging.rename(output)


def _build_frozen_presentation(source, output, presentation_binding, *,
                               include_english, language, authored_interpretation,
                               show_local_status_banners):
    """Render a bound historical report using saved scores and public evidence."""
    from harness.report_languages import select_report_language
    from .safety import evidence_tree_identity, copy_evidence_file
    if source in output.parents:
        raise ValueError("Frozen presentation output must be outside its source tree")
    before = evidence_tree_identity(source)
    _, dataset, records, manifest = validate_local_presentation(source, presentation_binding)
    if authored_interpretation is None:
        authored_interpretation = manifest.get('presentation', {}).get(
            'authored_interpretation', bool(dataset.get('comparison', {}).get('report_interpretation')))
    review_path = source / "result-review.json"
    binding = json.loads(presentation_binding.read_text())
    required = ["local-run-contract.json", "execution.json"]
    if review_path.is_file():
        required.append("result-review.json")
    for relative in required:
        if not any(item.get("path") == relative
                   and item.get("sha256") == before.get(relative)
                   for item in binding.get("evidence_bindings", [])):
            raise ValueError("Frozen presentation input lacks a hash binding: " + relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frozen-presentation-", dir=output.parent) as work:
        staging = Path(work) / "report"
        shutil.copytree(source, staging, copy_function=copy_evidence_file)
        shutil.rmtree(staging / "en", ignore_errors=True)
        shutil.copy2(presentation_binding, staging / "presentation-binding.json")
        batches = json.loads((source / "execution.json").read_text())["batches"]
        for record in records:
            case = next(item for item in dataset['cases'] if item['id'] == record['case_id'])
            if not record.get('diagnosis'):
                text = contained_path(source, record['artifact_path']).read_text(errors='replace')
                record['diagnosis'] = diagnose(record, case, text)
            metadata = record.setdefault("provider_metadata", {})
            metadata["public_log_note"] = dataset.get("comparison", {}).get(
                "call_chain_audit", {}).get("call_log_notes", {}).get(record["attempt_id"], "")
            for kind, suffix in (("debug_log", "debug"), ("response", "response")):
                relative = "logs/" + record["attempt_id"] + "." + suffix + ".txt"
                if relative in before:
                    metadata["public_" + kind + "_path"] = relative
                    metadata["public_" + kind + "_sha256"] = before[relative]
        generate(staging, dataset, records, batches, include_english=include_english,
                 authored_interpretation=authored_interpretation,
                 show_local_status_banners=show_local_status_banners, frozen_scores=True)
        # Scores remain frozen, but cached inventory/summary files must describe
        # those exact current scores after an explicitly bound offline revision.
        write_frozen_result_summary(staging, dataset, records)
        if language is not None:
            select_report_language(staging, language)
        write_frozen_presentation_manifest(staging, manifest, show_local_status_banners=show_local_status_banners,
                                           authored_interpretation=authored_interpretation)
        validate_local_presentation(staging, presentation_binding)
        if evidence_tree_identity(source) != before:
            raise ValueError("Presentation source changed during rendering")
        _install_report_tree(staging, output)
    return output / "index.html"


def write_frozen_presentation_manifest(output, manifest, *, show_local_status_banners=True, authored_interpretation=False):
    """Refresh renderer metadata while retaining the frozen measurement bindings."""
    from harness.report_languages import generated_languages
    from harness.reporting.templates import resolve_template
    current = dict(manifest)
    binding_path = output / 'presentation-binding.json'
    if binding_path.is_file():
        binding = json.loads(binding_path.read_text())
        if binding.get('offline_revision'):
            from .publication_privacy import read_evidence_binding
            authority = json.loads(read_evidence_binding(output, binding['offline_revision']).read_text())
            if authority.get('kind') == 'current_local_snapshot_authority':
                if authority.get('schema_version') != 1:
                    raise ValueError('Unsupported current presentation authority')
                current.pop('task_replacement', None)
                current.pop('task_replacement_current_scope', None)
                if authority.get('task_replacement_current_scope') is not None:
                    current['task_replacement_current_scope'] = authority['task_replacement_current_scope']
    languages = list(generated_languages(output))
    current.update(template=resolve_template('webfetch'), languages=languages, generated_languages=languages,
                   presentation={"show_local_status_banners": show_local_status_banners,
                                 "frozen_scores": True, "authored_interpretation": authored_interpretation})
    (output / "report-manifest.json").write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")


def write_frozen_result_summary(output, dataset, records):
    cases = {case['id']: case for case in dataset['cases']}
    summary = {}
    for track in TRACKS:
        providers = {}
        for provider in sorted({row['provider'] for row in records}):
            rows = [row for row in records if row['provider'] == provider
                    and cases[row['case_id']].get('track', 'web') == track]
            if not rows:
                continue
            eligible = [row for row in rows if measured(row, cases[row['case_id']])]
            text = [row for row in eligible if row['metrics'].get('content_check_coverage') is not None]
            item = {'attempts': len(rows), 'task_pass': sum(succeeded(row, cases[row['case_id']]) for row in rows),
                    'task_evaluated_attempts': len(eligible), 'task_unmeasured_attempts': len(rows) - len(eligible),
                    'task_excluded_attempts': sum(bool(row['metrics'].get('evaluation_excluded')) for row in rows),
                    'scoring_scope': scoring_scope(eligible, cases), 'text_checked_attempts': len(text),
                    'text_checked_cases': len({row['case_id'] for row in text})}
            if track == 'challenge':
                body = [row for row in eligible if cases[row['case_id']].get('task_kind', 'body') == 'body'
                        and cases[row['case_id']].get('ground_truth_main_content')]
                item.update(body_evaluated_attempts=len(body),
                            body_task_pass=sum(succeeded(row, cases[row['case_id']]) for row in body) if body else None)
            providers[provider] = item
        summary[track] = providers
    for name, value in [('summary.json', summary), ('reliability.json', assess_reliability(dataset, records))]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def build_report(source: Path, output: Path, *, include_english: bool = False,
                 local_audit: bool = False, authored_interpretation: bool | None = None,
                 diagnostic_preview: bool = False, language: str | None = None,
                 show_local_status_banners: bool = True,
                 presentation_binding: Path | None = None) -> Path:
    from harness.report_languages import includes_english, select_report_language
    include_english = includes_english(language, include_english)
    source, output = source.resolve(), output.resolve()
    if source == output or output in source.parents:
        raise ValueError('Report output must not contain its evidence source')
    marker = '.generated-report'
    if output.exists() and not (output/marker).is_file():
        raise ValueError(f'Refusing to replace an unmarked directory: {output}')
    if presentation_binding is not None:
        if not local_audit:
            raise ValueError("A frozen presentation binding requires local audit mode")
        return _build_frozen_presentation(
            source, output, presentation_binding, include_english=include_english,
            language=language, authored_interpretation=authored_interpretation,
            show_local_status_banners=show_local_status_banners)
    dataset_probe = json.loads(contained_path(source, 'dataset.json').read_text())
    diagnostic_preview = (diagnostic_preview and
        dataset_probe.get('comparison', {}).get('kind') == 'web_fetch_diagnostic')
    if not local_audit and not diagnostic_preview:
        from .reference_lifecycle import require_certifiable_evidence
        require_certifiable_evidence(dataset_probe)
    if dataset_probe.get('comparison', {}).get('kind') == 'webmainbench_frozen_html_to_markdown':
        return build_webmainbench_report(source, output, include_english=include_english,
                                         language=language)
    if not local_audit:
        require_selection_history(source)
    dataset = json.loads(contained_path(source,'dataset.json').read_text())
    if authored_interpretation is None:
        authored_interpretation = bool(dataset.get('comparison', {}).get('report_interpretation'))
    records = load_jsonl(contained_path(source,'results.jsonl'))
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
        if speed.get('bindings',{}).get('dataset_sha256') != file_sha256(speed_root/'dataset.json'):
            raise ValueError('Speed comparison dataset binding mismatch')
        if speed.get('bindings',{}).get('results_sha256') != file_sha256(speed_root/'results.jsonl'):
            raise ValueError('Speed comparison result binding mismatch')
        speed_dataset=json.loads((speed_root/'dataset.json').read_text())
        speed_records=load_jsonl(speed_root/'results.jsonl')
        speed_execution=speed_root/'execution.json'
        if speed_execution.is_file():
            speed_batches=json.loads(speed_execution.read_text()).get('batches', [])
        for row in speed_records:
            verify_record_private_artifacts(speed_root, row)
            verify_record_public_call_logs(speed_root, row)
            raw=contained_path(speed_root,row['artifact_path'])
            if file_sha256(raw)!=row['metrics']['content_sha256']:
                raise ValueError('Speed cohort output integrity failure: '+row['attempt_id'])
    resource_root=source/'resource-cohort'
    if resource_root.exists():
        from .resource_cohort import validate_cohort
        resource_dataset, resource_records, resource_batches = validate_cohort(resource_root)
    for c in cases.values():
        annotation=c.get('source_benchmark',{}).get('stable_annotation',{})
        if annotation.get('anchor_evidence_sha256'):
            if (local_audit or dataset.get('comparison', {}).get('reference_binding_mode') == 'frozen_task_contracts'
                    or dataset.get('comparison', {}).get('source_reference_policy') in {
                        'frozen_acceptance_contracts_with_live_exact_reference_checks',
                        'frozen_benchmark_inputs_with_explicit_live_exact_checks'}):
                # The two historical dynamic cohorts retain their own source
                # audit receipts.  Do not reinterpret those revisions with the
                # newer origin/main release gate in a local merged view.
                continue
            if _validate_stable_annotation_evidence(c, source, dataset) != annotation['anchor_evidence_sha256']:
                raise ValueError('Stable text evidence changed: '+c['id'])
    for r in records:
        if r.get('metrics', {}).get('scoring_source_sha256') != SCORING_SOURCE_SHA256:
            raise ValueError(
                f'Stale scoring implementation; rescore before reporting: {r["attempt_id"]}')
        contract = cases[r['case_id']].get('acceptance_contract')
        if contract:
            from lexbench_webfetch.stable_tasks import validate_acceptance_contract
            validate_acceptance_contract(contract)
            expected = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if r['metrics'].get('acceptance_contract_sha256') != expected:
                raise ValueError(f'Acceptance contract hash missing or mismatched: {r["attempt_id"]}')
        composite = cases[r['case_id']].get('composite_acceptance')
        if composite:
            from lexbench_webfetch.composite_tasks import validate_composite_acceptance
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
    if not cases or not records:
        raise ValueError('A report requires tasks and measured records')
    execution = source/'execution.json'
    manifests = json.loads(execution.read_text())['batches'] if execution.exists() else [json.loads((source/'manifest.json').read_text())]
    from .native_routes import verify_native_execution_manifests
    verify_native_execution_manifests(source, manifests, dataset, records)
    if not local_audit and not diagnostic_preview:
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
            if file_sha256(raw) != r['metrics']['content_sha256']:
                raise ValueError(f'Output integrity failure: {path}')
            target = staging/path; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw, target)
            meta = r.get('provider_metadata', {})
            if not local_audit:
                # A portable report cannot link to its former sibling private tree.
                # The full raw logs were verified above and stay in the evidence bundle.
                meta.pop('local_report_complete_diagnostic_links', None)
            if meta.get('rendered_html_path'):
                html_source = contained_path(source, meta['rendered_html_path'])
                if file_sha256(html_source) != meta.get('rendered_html_sha256'):
                    raise ValueError('Rendered HTML integrity failure: ' + r['attempt_id'])
                html_target = contained_path(staging, meta['rendered_html_path'])
                html_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(html_source, html_target)
            if meta.get('raw_stdout_path'):
                binary_source = contained_path(source, meta['raw_stdout_path'])
                if file_sha256(binary_source) != meta.get('raw_stdout_sha256'):
                    raise ValueError('Raw stdout integrity failure: ' + r['attempt_id'])
                binary_target = contained_path(staging, meta['raw_stdout_path'])
                binary_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(binary_source, binary_target)
            for file in r.get('provider_metadata', {}).get('file_evaluation', {}).get('files', []):
                binary = contained_path(source, file['artifact_path'])
                if file_sha256(binary) != file['sha256']:
                    raise ValueError('File output integrity failure: '+r['attempt_id'])
                target_file = contained_path(staging, file['artifact_path'])
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(binary, target_file)
            if local_audit:
                meta["public_log_note"] = dataset.get("comparison", {}).get("call_chain_audit", {}).get("call_log_notes", {}).get(r["attempt_id"], "")
            if local_audit and meta.get("diagnostic_log_path"):
                from .runner import _public_text
                diagnostic_source = contained_path(source, meta["diagnostic_log_path"])
                from .report import runtime_log_text
                raw_log = runtime_log_text(diagnostic_source.read_text(encoding="utf-8", errors="replace"))
                safe_log = _public_text(raw_log, limit=len(raw_log)) or ""
                public_log = "logs/" + r["attempt_id"] + ".debug.txt"
                destination = contained_path(staging, public_log)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(safe_log, encoding="utf-8")
                meta["public_debug_log_path"] = public_log
                meta["public_debug_log_sha256"] = file_sha256(destination)
            if local_audit and meta.get("raw_response_path"):
                from .runner import _public_text
                response_source = contained_path(source, meta["raw_response_path"])
                response_text = response_source.read_text(encoding="utf-8", errors="replace")
                public_response = "logs/" + r["attempt_id"] + ".response.txt"
                destination = contained_path(staging, public_response)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(_public_text(response_text, limit=len(response_text)) or "", encoding="utf-8")
                meta["public_response_path"] = public_response
                meta["public_response_sha256"] = file_sha256(destination)
            for binding in record_public_call_log_bindings(r):
                log_source = verify_public_call_log(source, binding)
                call_log = json.loads(log_source.read_text())
                call_output = call_log['output']
                call_output_source = contained_path(source, call_output['path'])
                call_output_target = contained_path(staging, call_output['path'])
                call_output_target.parent.mkdir(parents=True, exist_ok=True)
                if call_output_target.exists():
                    if file_sha256(call_output_target) != call_output['sha256']:
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
                r['diagnosis']=diagnose(r,cases[r['case_id']],text)
            r['failure_category'] = failure_category(r, cases[r['case_id']])
        for folder in ['reference-checks']:
            tree=contained_path(source,folder,reject_symlinks=True)
            if tree.exists():
                reject_evidence_symlinks(tree)
        materialize_task_references(staging, {name: source/name
            for name in ('sources','list-references')}, cases)
        materialize_reference_checks(staging, source, manifests)
        if speed_root.exists():
            copy_cohort_evidence(
                speed_root, staging/'speed-cohort', speed_dataset, speed_records, speed_batches,
                summary_name='speed-comparison.json')
        if resource_root.exists():
            copy_cohort_evidence(resource_root, staging/'resource-cohort',
                resource_dataset, resource_records, resource_batches,
                summary_name='resource-comparison.json')
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
        # Preserve frozen input bytes across build and release profile checks.
        shutil.copy2(contained_path(source,'dataset.json'), staging/'dataset.json')
        for name,data in [('execution.json',{'batches':manifests}),('summary.json',summary)]:
            (staging/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        if (source / 'certification.json').is_file():
            shutil.copy2(source / 'certification.json', staging / 'certification.json')
            if dataset.get('comparison', {}).get('kind') == 'risk_control_100_moli':
                from .risk_control import risk_control_verification_binding
                receipt = json.loads((source / 'certification.json').read_text())
                verification_name, _ = risk_control_verification_binding(receipt)
                for name in ('dataset.json', 'manifest.json', 'execution.json', verification_name):
                    shutil.copy2(contained_path(source, name), staging / name)
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
        content_binding = dataset.get('comparison', {}).get('report_interpretation')
        if content_binding is not None:
            copy_bound_assets(source, staging, {content_binding['path']: content_binding['sha256']})
            authored_content = json.loads(contained_path(staging, content_binding['path']).read_text())
            nested_assets = {item['path']: item['sha256'] for item in authored_content.get('figures', [])}
            failure_binding = authored_content.get('failure_review')
            if failure_binding:
                copy_bound_assets(source, staging, {failure_binding['path']: failure_binding['sha256']})
                failure_review = json.loads(contained_path(staging, failure_binding['path']).read_text())
                for item in failure_review.get('failures', []):
                    for evidence in item.get('evidence_paths', []):
                        if not isinstance(evidence, dict):
                            raise ValueError('Failure review evidence requires path and sha256')
                        previous = nested_assets.setdefault(evidence['path'], evidence['sha256'])
                        if previous != evidence['sha256']:
                            raise ValueError('Conflicting interpretation asset binding')
            missing_assets = {}
            for relative, expected_hash in nested_assets.items():
                existing = contained_path(staging, relative, reject_symlinks=True)
                if existing.exists():
                    if not existing.is_file() or file_sha256(existing) != expected_hash:
                        raise ValueError('Conflicting interpretation evidence: ' + relative)
                else:
                    missing_assets[relative] = expected_hash
            if missing_assets:
                copy_bound_assets(source, staging, missing_assets)
        copy_methodology_assets(staging)
        implementation_binding = dataset.get('comparison', {}).get('implementation_provenance')
        if implementation_binding is not None:
            if implementation_binding['path'] != 'implementation-provenance.json':
                raise ValueError('Implementation provenance uses implementation-provenance.json')
            copy_bound_assets(source, staging, {
                implementation_binding['path']: implementation_binding['sha256']})
        else:
            provenance=Path(__file__).resolve().parents[2]/'research/headless-implementation-provenance.json'
            if provenance.is_file():
                payload=json.loads(provenance.read_text())
                hashes={r.get('provider_metadata',{}).get('binary_sha256') for r in records}
                matched=[item for item in payload.get('implementations',[]) if item.get('binary_sha256') in hashes]
                if matched:
                    (staging/'implementation-provenance.json').write_text(json.dumps(
                        {**payload,'implementations':matched},ensure_ascii=False,indent=2)+'\n')
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
                actual = file_sha256(archived)
                if not expected or actual != expected:
                    raise ValueError('Archived result review hash does not match scoring receipt')
                target = staging/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archived, target)
        if (source/'reference-revisions.json').is_file():
            shutil.copy2(source/'reference-revisions.json', staging/'reference-revisions.json')
        replacement_scopes = source/'provider-replacement-scopes.json'
        if replacement_scopes.is_file():
            scopes = json.loads(replacement_scopes.read_text())
            if not isinstance(scopes, list):
                raise ValueError('Provider replacement scope index is invalid')
            for item in scopes:
                if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'replacements'}):
                    raise ValueError('Provider replacement scope entry is invalid')
                bound = contained_path(source, item['path'], reject_symlinks=True)
                if (not bound.is_file()
                        or file_sha256(bound) != item['sha256']):
                    raise ValueError('Provider replacement scope receipt changed')
                target = contained_path(staging, item['path'], reject_symlinks=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bound, target)
            shutil.copy2(replacement_scopes, staging/'provider-replacement-scopes.json')
        if (source/'report-binding-revisions.json').is_file():
            receipts = json.loads((source/'report-binding-revisions.json').read_text())
            bindings = {row[f'{side}_dataset_path']: row[f'{side}_dataset_sha256']
                        for row in receipts for side in ('input', 'output')}
            copy_bound_assets(source, staging, bindings)
            shutil.copy2(source/'report-binding-revisions.json', staging/'report-binding-revisions.json')
        if (source/'latency-corrections.json').is_file():
            shutil.copy2(source/'latency-corrections.json', staging/'latency-corrections.json')
        if (source/'request-status-corrections.json').is_file():
            shutil.copy2(source/'request-status-corrections.json',
                         staging/'request-status-corrections.json')
        if (source/'file-rerun-selection.json').is_file():
            shutil.copy2(source/'file-rerun-selection.json', staging/'file-rerun-selection.json')
            shutil.copytree(source/'history/file-baseline', staging/'history/file-baseline')
        if dataset.get("comparison", {}).get("kind") == "uniform_native_local_measurement":
            shutil.copy2(source / "local-run-contract.json", staging / "local-run-contract.json")
            if (source/"providers.json").is_file():
                from .report import NATIVE_OPTION_LABELS
                from .publication_privacy import require_public_method_options
                config_bytes = contained_path(source, 'providers.json', reject_symlinks=True).read_bytes()
                config_hash = hashlib.sha256(config_bytes).hexdigest()
                contract = json.loads((source / 'local-run-contract.json').read_text())
                if config_hash != contract.get('provider_config_sha256'):
                    raise ValueError('Frozen option configuration differs from the local run contract')
                config = json.loads(config_bytes)['providers']
                methods = sorted({row['provider'] for row in records})
                public_options = {name: {key: value for key, value in config[name].items()
                                         if key in NATIVE_OPTION_LABELS} for name in methods}
                public_config = {'providers': public_options,
                    'projection': 'allowlisted_method_options',
                    'original_provider_config_sha256': config_hash}
                public_bytes = (json.dumps(public_config, ensure_ascii=False, indent=2) + '\n').encode()
                require_public_method_options(public_options)
                relative = 'local-evidence/public-method-options.json'
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(public_bytes)
                options = {'schema_version': 2, 'provider_config_sha256': config_hash,
                    'source_config': {'path': relative, 'sha256': hashlib.sha256(public_bytes).hexdigest()},
                    'case_ids': sorted({row['case_id'] for row in records}),
                    'result_count': len(records), 'providers': public_options,
                    'scope_title': '本次调用保存的固定选项',
                    'scope_title_en': 'Recorded options for these calls',
                    'scope_description': '仅展示本报告调用配置中明确设置的公开选项；未列出的参数不代表关闭。原始配置私下保留，公开副本仅包含允许展示的字段。',
                    'scope_description_en': 'Only explicitly configured public options for this report are shown; omitted parameters do not mean disabled. The original configuration remains private; this public copy contains only allowed fields.'}
                (staging / 'frozen-method-options.json').write_text(
                    json.dumps(options, ensure_ascii=False, indent=2) + '\n')
            from harness.reporting.templates import resolve_template
            (staging / "report-manifest.json").write_text(json.dumps({
                "schema_version": 1, "template": resolve_template("webfetch"),
                "evidence_state": "local_measurement", "certified": False,
                "languages": ["zh", "en"] if include_english else ["zh"], "provider_calls_added": 0,
                "dataset_sha256": file_sha256(source / "dataset.json"),
                "results_sha256": file_sha256(source / "results.jsonl"),
                "presentation": {"show_local_status_banners": show_local_status_banners},
            }, ensure_ascii=False, indent=2))
        from .report_reading import copy_archived_reading
        copy_archived_reading(source,staging)
        generate(staging,dataset,records,manifests,include_english=include_english,
                 authored_interpretation=authored_interpretation,
                 show_local_status_banners=show_local_status_banners)
        if language is not None:
            select_report_language(staging, language)
        from harness.reporting.templates import resolve_template
        from harness.report_languages import generated_languages
        template_manifest = {'schema_version': 1, 'template': None if diagnostic_preview else resolve_template('webfetch'),
            'dataset_sha256': file_sha256(staging/'dataset.json'),
            'results_sha256': file_sha256(staging/'results.jsonl'),
            'generated_languages': generated_languages(staging)}
        if dataset.get('comparison', {}).get('kind') == 'uniform_native_local_measurement':
            template_manifest.update(evidence_state='local_measurement', certified=False,
                languages=template_manifest['generated_languages'], provider_calls_added=0)
        (staging/'report-manifest.json').write_text(json.dumps(template_manifest, ensure_ascii=False, indent=2)+'\n')
        (staging/marker).write_text('Generated from persisted evidence. Rebuild; do not edit.\n')
        # Replace only an explicitly marked generated directory; never merge stale pages.
        _install_report_tree(staging, output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    return output/('en/index.html' if language == 'en' else 'index.html')


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
        actual = file_sha256(contained_path(source, filename))
        if bindings.get(binding) != actual:
            raise ValueError(f'WebMainBench anomaly review {binding} mismatch')
    if bindings.get('results_jsonl_sha256') != file_sha256(prior_results_path(source)):
        raise ValueError('WebMainBench anomaly review results_jsonl_sha256 mismatch')
    if (bindings.get('source_dataset_sha256') != dataset.get('source_dataset_sha256')
            or bindings.get('sample_manifest_sha256') != dataset.get('sample_manifest_sha256')):
        raise ValueError('WebMainBench anomaly review dataset binding mismatch')
    retest_sha = receipt.get('diagnostic_evidence', {}).get('moli_origin_retest_summary_sha256')
    if retest_sha:
        retest_path = contained_path(source, 'latency-diagnostic/failure-retest/summary.json')
        if not retest_path.is_file() or file_sha256(retest_path) != retest_sha:
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


def build_webmainbench_report(source: Path, output: Path, *, include_english: bool = False,
                                language: str | None = None) -> Path:
    """Build the current public-benchmark report without legacy task predicates."""
    from harness.report_languages import includes_english, select_report_language
    include_english = includes_english(language, include_english)
    from harness.reporting.assets import copy_bound_assets, load_bound_content

    authority = Path(__file__).resolve().parents[2]
    content_binding_path = authority / 'evaluation/webmainbench-content-binding.json'
    content_binding = json.loads(content_binding_path.read_text(encoding='utf-8'))
    content_source = data_home() / 'report-assets/webmainbench-sample'
    report_content = load_bound_content(
        content_source, content_binding, evidence_root=source, authority_root=authority)
    dataset = json.loads(contained_path(source, 'dataset.json').read_text())
    records = load_jsonl(contained_path(source, 'results.jsonl'), skip_blank=True)
    teds_revision = validate_teds_revision_if_present(source, records)
    summaries = json.loads(contained_path(source, 'score-summaries.json').read_text())
    manifests = json.loads(contained_path(source, 'execution.json').read_text())['batches']
    latency_diagnostic = load_latency_diagnostic(source, dataset, records, manifests)
    review_path = source / 'result-review.json'
    review = json.loads(review_path.read_text()) if review_path.is_file() else None
    if review is not None and review.get('bindings', {}).get('results_jsonl_sha256') != file_sha256(prior_results_path(source)):
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
    if not parent.is_file() or file_sha256(parent) != dataset['source_dataset_sha256']:
        raise ValueError('Frozen WebMainBench parent dataset is unavailable or differs from the bound source hash')
    parent_rows = load_jsonl(parent, skip_blank=True)
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
            if file_sha256(artifact) != row['output_sha256']:
                raise ValueError('WebMainBench output integrity failure: ' + row['attempt_id'])
        if latency_diagnostic is not None:
            (staging / 'latency-diagnostic.json').write_text(
                json.dumps(latency_diagnostic, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        copy_bound_assets(content_source, staging, {
            content_binding['relative_path']: content_binding['sha256']})
        shutil.copy2(content_binding_path, staging / 'report-content-binding.json')
        generate_webmainbench(staging, dataset, records, summaries, manifests, review, research, corrections, adoption, fixture_correction, teds_revision, anomaly_review, latency_diagnostic, include_english=include_english, report_content=report_content)
        if language is not None:
            select_report_language(staging, language)
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
    return output / ('en/index.html' if language == 'en' else 'index.html')


def public_webmainbench_scoring_projection(records: list[dict]) -> list[dict]:
    """Omit private predecessor metrics while retaining certified replacement rows verbatim."""
    projected = []
    for record in records:
        row = dict(record)
        if not row.get('supersedes_attempt_id'):
            row.pop('pre_scoring_correction_metrics', None)
        projected.append(row)
    return projected


def build_webmainbench_full_report(source: Path, output: Path) -> Path:
    """Build the separately bound complete 545-case report from certified evidence."""
    from harness.web_fetch.webmainbench_evidence import digest, read_jsonl

    from .webmainbench_diagnostics import (
        validate_reference_revision,
        validate_teds_revision,
        validate_webmainbench_scoring_revision,
    )
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
    repo = Path(__file__).resolve().parents[2]
    registry_path = repo / 'evaluation/webmainbench-full/moli-variants.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    from .webmainbench_evidence import read_method_binding
    binding_name, method_binding = read_method_binding(source)
    teds_path = source / 'teds-correction.json'
    teds_receipt = json.loads(teds_path.read_text(encoding='utf-8')) if teds_path.is_file() else None
    scoring_path = source / 'scoring-correction.json'
    scoring_receipt = json.loads(scoring_path.read_text(encoding='utf-8')) if scoring_path.is_file() else None
    variants = registry.get('variants', [])
    bound_variants = {item['provider']: item for item in method_binding.get('variants', [])}
    if (method_binding.get('kind') not in {'webmainbench_seven_method_full_scope_binding', 'webmainbench_method_cohort_binding'}
            or method_binding.get('registry_sha256') != digest(registry_path)
            or method_binding.get('baseline_profile_sha256') != digest(profile_path)
            or method_binding.get('baseline_certificate_sha256') != digest(source / 'cohort-certificate.json')
            or method_binding.get('results_sha256') != (
                teds_receipt.get('input_results_sha256') if teds_receipt
                else scoring_receipt.get('input_results_sha256') if scoring_receipt else None)
            or (teds_receipt is not None and teds_receipt.get('output_results_sha256') != digest(
                source / ('pre-reference-correction-results.jsonl'
                          if (source / 'reference-correction.json').is_file()
                          else 'pre-scoring-correction-results.jsonl'
                          if scoring_receipt else 'results.jsonl')))
            or method_binding.get('logical_results') != 545 * (len(profile['providers']) + len(variants))):
        raise ValueError('Seven-method report evidence binding mismatch')
    versions = dict(profile['versions'])
    variant_labels = {}
    for item in variants:
        provider = item['provider']
        bound = bound_variants.get(provider, {})
        profile_path_variant = repo / 'evaluation/webmainbench-full' / item['profile']
        variant_profile = json.loads(profile_path_variant.read_text(encoding='utf-8'))
        cert_path = source / bound.get('cohort_certificate', '')
        scoring_path = source / bound.get('scoring_certificate', '')
        cert = json.loads(cert_path.read_text(encoding='utf-8'))
        version = variant_profile.get('versions', {}).get('moli')
        if (not isinstance(version, str) or not version.strip()
                or bound.get('profile_sha256') != digest(profile_path_variant)
                or bound.get('cohort_certificate_sha256') != digest(cert_path)
                or bound.get('scoring_certificate_sha256') != digest(scoring_path)
                or cert.get('preflight') != cert.get('postflight')
                or cert.get('profile_sha256') != digest(profile_path_variant)
                or cert.get('binary_source_commit') != variant_profile['source_commit']
                or cert.get('measured_binary_sha256') != variant_profile['binary_sha256']):
            raise ValueError('Moli repair variant was not certified: ' + provider)
        versions[provider] = version
        variant_labels[provider] = item['label']
    providers = profile['providers'] + [item['provider'] for item in variants]
    display_profile = {**profile, 'providers': providers, 'versions': versions,
                       'variant_labels': variant_labels, 'variants': variants, 'method_binding_file': binding_name}
    cases = {case['id']: case for case in dataset['cases']}
    if (len(cases) != 545 or len(records) != 545 * len(providers)
            or {(row['provider'], row['case_id']) for row in records} !=
            {(provider, case_id) for provider in providers for case_id in cases}):
        raise ValueError('Full WebMainBench result matrix mismatch')
    if teds_receipt is not None:
        validate_teds_revision(source)
    if (source / 'reference-correction.json').is_file():
        validate_reference_revision(source)
    if (source / 'scoring-correction.json').is_file():
        validate_webmainbench_scoring_revision(source)
    content_binding_path = Path(__file__).resolve().parents[2] / 'evaluation/webmainbench-full/content-binding.json'
    content_binding = json.loads(content_binding_path.read_text(encoding='utf-8'))
    content_path = data_home() / 'report-assets/webmainbench-full' / content_binding['relative_path']
    from harness.reporting.assets import load_bound_content
    load_bound_content(data_home() / 'report-assets/webmainbench-full', content_binding)
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
        # The origin correction was certified against the raw seven-method matrix.
        # Later score-only TEDS/reference revisions preserve those provider bytes.
        case_correction_results_path = source / 'original-results.jsonl'
        case_correction_records = read_jsonl(case_correction_results_path)
        _, corrected_rows, _ = validate_case_correction(
            correction_root, case_correction_records, digest(case_correction_results_path)
        )
        corrected_keys = {(r['provider'], r['case_id']) for r in corrected_rows}
    failures = {(row['provider'], row['case_id']) for row in records
                if webmainbench_full_machine_issue(source, row)} - corrected_keys
    reviewed = {(row['provider'], row['case_id']) for row in failure_analysis['reviewed_failures']}
    if (failure_analysis.get('kind') != 'webmainbench_full_failure_analysis_v1'
            or failure_analysis.get('cohort_certificate_sha256') != digest(source / 'cohort-certificate.json')
            or reviewed != {item for item in failures if item[0] not in variant_labels}
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
        for name in ('artifacts', 'references', 'sources', 'fixture-certificates'):
            shutil.copytree(source / name, staging / name)
        if (source / 'correction-source').is_dir():
            shutil.copytree(source / 'correction-source', staging / 'correction-source')
        for name in ('dataset.json', 'results.jsonl',
                     'physical-attempts.jsonl', 'execution.json', 'score-summaries.json',
                     'cohort-certificate.json', 'current-report.json',
                     'scoring-certificate.json',
                     binding_name):
            shutil.copy2(source / name, staging / name)
        if teds_receipt is not None:
            for name in ('teds-correction.json', 'teds-correction-review.json'):
                shutil.copy2(source / name, staging / name)
        if (source / 'reference-correction.json').is_file():
            shutil.copytree(source / 'reference-correction-source', staging / 'reference-correction-source')
            shutil.copy2(source / 'reference-correction.json', staging / 'reference-correction.json')
        if (source / 'scoring-correction.json').is_file():
            shutil.copytree(source / 'scoring-correction-source', staging / 'scoring-correction-source')
            shutil.copy2(source / 'scoring-correction.json', staging / 'scoring-correction.json')
        for item in variants:
            provider = item['provider']
            bound = bound_variants[provider]
            for name in (item['profile'], bound['cohort_certificate'], bound['scoring_certificate']):
                shutil.copy2(source / name, staging / name)
        shutil.copy2(content_path, staging / 'report-content.json')
        shutil.copy2(failure_path, staging / 'failure-analysis.json')
        shutil.copy2(Path(__file__).resolve().parents[2] / 'research/provider-adoption.json', staging / 'provider-adoption.json')
        summaries = json.loads((source / 'score-summaries.json').read_text(encoding='utf-8'))
        if correction_root:
            from .webmainbench_correction import apply as apply_case_correction
            raw_records = read_jsonl(source / 'original-results.jsonl')
            records, summaries = apply_case_correction(
                correction_root, staging, records, summaries,
                validation_rows=raw_records,
                expected_parent=digest(source / 'original-results.jsonl'),
            )
            case_scoring_receipt = correction_root / 'scoring-correction.json'
            if case_scoring_receipt.is_file():
                shutil.copy2(case_scoring_receipt, staging / 'case-correction-scoring.json')
        records = public_webmainbench_scoring_projection(records)
        (staging / 'results.jsonl').write_text(
            ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records),
            encoding='utf-8',
        )
        if correction_root:
            case_receipt_path = staging / 'case-correction.json'
            case_receipt = json.loads(case_receipt_path.read_text(encoding='utf-8'))
            case_receipt['current_results_sha256'] = digest(staging / 'results.jsonl')
            case_receipt_path.write_text(
                json.dumps(case_receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
            )
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
        from harness.web_fetch.webmainbench_report_check import (
            check as check_webmainbench_full,
        )
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


def build_wcxb_report(source: Path, corpus: Path, sample_path: Path, profile: dict, output: Path,
                      *, content_binding: dict, authority_root: Path,
                      include_english: bool = False, language: str | None = None) -> Path:
    """Build the one current report from a fully certified WCXB matrix."""
    from harness.report_languages import includes_english, select_report_language
    include_english = includes_english(language, include_english)
    from harness.web_fetch.wcxb_dataset import validate_manifest
    from harness.web_fetch.webmainbench_runner import WCXB_PROVIDERS, load_wcxb_source
    source, output = source.resolve(), output.resolve()
    from harness.reporting.assets import load_bound_content
    report_content = load_bound_content(
        source / "report-assets", content_binding,
        evidence_root=source, authority_root=authority_root)
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
    evaluator_hash = file_sha256(contained_path(corpus, 'evaluate.py'))
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
    rows = load_jsonl(source / 'wcxb-scores.jsonl', skip_blank=True)
    case_ids = {r['track_id'] for r in records}
    correction_root = source / 'latency-correction'
    correction_receipt = correction_root / 'receipt.json'
    if (not correction_receipt.is_file()
            or file_sha256(correction_receipt)
            != profile.get('latency_correction_receipt_sha256')):
        raise ValueError('WCXB full-scope latency correction is missing or differs from profile')
    _, corrected_calls = load_full_paired_latency(correction_root, profile, case_ids)
    correction_certificate = json.loads((correction_root / 'certification.json').read_text())
    if (correction_certificate.get('kind') != 'certified_wcxb_paired_latency'
            or correction_certificate.get('complete') is not True
            or correction_certificate.get('receipt_sha256')
            != file_sha256(correction_receipt)):
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
            or file_sha256(optional_receipt)
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
                    or file_sha256(artifact) != optional['builds'][name][key]):
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
        copy_bound_assets(source / 'report-assets', staging, {
            name: digest for name, digest in profile['report_assets'].items()
            if include_english or name != 'content/wcxb-repair-en.fragment'
        })
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
            if file_sha256(results_file) != batch['results_sha256']:
                raise ValueError('WCXB results changed: ' + provider)
            if file_sha256(run_dir / 'physical-attempts.jsonl') != summary['providers'][provider]['physical_attempts_sha256']:
                raise ValueError('WCXB physical attempts changed: ' + provider)
            manifest = json.loads((run_dir / 'execution.json').read_text())
            if manifest['run_id'] != batch['run_id'] or manifest['source_dataset_sha256'] != corpus_hash:
                raise ValueError('WCXB run identity changed: ' + provider)
            identities[provider] = manifest['implementation_identity']
            raw_rows = load_jsonl(results_file, skip_blank=True)
            if len(raw_rows) != len(records):
                raise ValueError('WCXB provider results incomplete: ' + provider)
            for row in raw_rows:
                artifact = contained_path(run_dir, row['artifact_path'])
                if artifact.is_symlink() or file_sha256(artifact) != row['output_sha256']:
                    raise ValueError('WCXB output changed: ' + row['attempt_id'])
                target = staging / 'artifacts' / provider / f'{row["track_id"]}.txt'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact, target)
            for scored in (r for r in rows if r['provider'] == provider):
                for retry in scored.get('retry_failures', []):
                    artifact = contained_path(source, retry['artifact_path'])
                    if artifact.is_symlink() or file_sha256(artifact) != retry['output_sha256']:
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
        generate_wcxb(staging, rows, summary, ground_truth, identities, profile, report_content,
                      include_english=include_english)
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
            evidence_hashes[case_id] = file_sha256(target)
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
        if language is not None:
            select_report_language(staging, language)
        from .wcxb_publication import write_inventory
        write_inventory(staging, profile)
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
    return output / ('en/index.html' if language == 'en' else 'index.html')


def validate_local_presentation(output: Path, binding_path: Path):
    """Refresh explanatory routes only; never validate or rescore with a new scorer.

    This is a presentation update of an existing hash-bound diagnostic snapshot,
    not a fresh build or certification. All measured files remain untouched.
    """
    from lexbench_webfetch.paths import contained_path
    from harness.report_languages import generated_languages
    output = output.resolve()
    binding = json.loads(binding_path.read_text())
    manifest_path = contained_path(output, 'report-manifest.json')
    manifest = json.loads(manifest_path.read_text())
    if (binding.get('comparison_kind') != 'uniform_native_local_measurement'
            or manifest.get('evidence_state') != 'local_measurement'
            or manifest.get('certified') is not False
            or manifest.get('languages') != list(generated_languages(output))
            or not (output / '.generated-report').is_file()):
        raise ValueError('Presentation refresh requires a bound diagnostic snapshot')
    for name, key in [('dataset.json', 'frozen_dataset_sha256'), ('results.jsonl', 'results_sha256')]:
        expected = binding.get(key)
        manifest_key = 'dataset_sha256' if name == 'dataset.json' else key
        if (not expected or manifest.get(manifest_key) != expected
                or file_sha256(contained_path(output, name)) != expected):
            raise ValueError('Presentation input binding changed: ' + name)
    for item in binding.get('evidence_bindings', []):
        path = contained_path(output, item['path'], reject_symlinks=True)
        if file_sha256(path) != item['sha256']:
            raise ValueError('Presentation evidence binding changed: ' + item['path'])
    if binding.get('publication_projection'):
        from .publication_privacy import require_host_metadata_projection
        require_host_metadata_projection(output, binding['publication_projection'])
    dataset = json.loads((output / 'dataset.json').read_text())
    records = load_jsonl(contained_path(output, 'results.jsonl'))
    if binding.get('publication_evidence_views'):
        from .publication_privacy import require_public_evidence_view_inventory
        require_public_evidence_view_inventory(output, binding['publication_evidence_views'], records)
    from .native_routes import verify_native_execution_manifests
    execution = output/'execution.json'
    if execution.is_file():
        verify_native_execution_manifests(output, json.loads(execution.read_text())['batches'], dataset, records)
    scoring_sources = {row['attempt_id']: binding.get('measured_scoring_source_sha256') for row in records}
    if binding.get('offline_revision'):
        from .local_revision import validate_revision_chain
        from .publication_privacy import read_evidence_binding
        scoring_sources, input_dataset, input_results = validate_revision_chain(
            output, binding['offline_revision'], dataset, records)
        revision_authority = json.loads(read_evidence_binding(output, binding['offline_revision']).read_text())
        if revision_authority.get('kind') == 'current_local_snapshot_authority':
            from .local_revision import require_current_task_replacement
            if (revision_authority.get('schema_version') != 1
                    or input_dataset is not None or input_results is not None
                    or binding.get('task_replacement')):
                raise ValueError('Current presentation authority must bind current task scope, not deleted history')
            current_scope = revision_authority.get('task_replacement_current_scope')
            if binding.get('task_replacement_current_scope') != current_scope:
                raise ValueError('Presentation current task replacement scope differs')
            if current_scope is not None:
                require_current_task_replacement(output, revision_authority, dataset, records)
        else:
            if input_dataset is None or input_results is None:
                raise ValueError('Historical presentation revision has no input snapshot')
            if binding.get('task_replacement'):
                from .local_revision import require_local_task_replacement
                previous_dataset = json.loads(read_evidence_binding(output, input_dataset).read_text())
                previous_records = load_jsonl(read_evidence_binding(output, input_results))
                require_local_task_replacement(output, binding['task_replacement'], previous_dataset, previous_records)
    elif binding.get('task_replacement'):
        from .local_revision import require_local_task_replacement
        scoring_sources = require_local_task_replacement(output, binding['task_replacement'], dataset, records)
    if (len(records) != binding.get('result_count')
            or len(dataset['cases']) != binding.get('case_count')
            or sorted({row['provider'] for row in records}) != sorted(binding['providers'])
            or any(row['metrics'].get('scoring_source_sha256') != scoring_sources[row['attempt_id']] for row in records)):
        raise ValueError('Presentation refresh cannot change the measured matrix or scoring revision')
    if any(path.is_symlink() for path in output.rglob("*")):
        raise ValueError("Presentation snapshot must not contain symlinks")
    validate_report_ids(dataset, records)
    reliability = assess_reliability(dataset, records)
    from .cohort_exclusions import require_cohort_exclusions
    excluded = require_cohort_exclusions(output, dataset, records)
    if (not reliability['complete'] and
            (any(reliability[key] for key in ('missing', 'extra', 'duplicates'))
             or any(item['case_id'] not in excluded for item in reliability['unjudged']))):
        raise ValueError('Presentation refresh requires the complete declared matrix')
    for row in records:
        from .publication_privacy import read_evidence_binding
        try:
            read_evidence_binding(output, {'path': row['artifact_path'],
                                           'sha256': row['metrics']['content_sha256']})
        except ValueError as error:
            if binding.get('publication_evidence_views'):
                raise
            raise ValueError('Saved output changed: ' + row['attempt_id']) from error
        verify_record_public_call_logs(output, row,
            allow_public_views=bool(binding.get('publication_evidence_views')))
    return binding, dataset, records, manifest


def _review_refresh_changes(output, binding, dataset, records):
    """Derive refresh scope from bound old inputs, never an unchecked ID list."""
    baseline = binding.get('review_refresh_baseline')
    if baseline is None:
        return set(), set()
    from .publication_privacy import read_evidence_binding
    before_dataset = json.loads(read_evidence_binding(output, baseline['dataset']).read_text())
    before_records = load_jsonl(read_evidence_binding(output, baseline['results']))
    old_cases = {case['id']: case for case in before_dataset['cases']}
    cases = {case['id']: case for case in dataset['cases']}
    old_rows = {row['attempt_id']: row for row in before_records}
    rows = {row['attempt_id']: row for row in records}
    if (len(old_rows) != len(before_records) or len(rows) != len(records)
            or old_rows.keys() != rows.keys() or old_cases.keys() != cases.keys()):
        raise ValueError('Review refresh cannot change the case or attempt inventory')
    changed_cases = {identifier for identifier in cases if old_cases[identifier] != cases[identifier]}
    # Scoring timestamps/provenance alone do not change reader-facing results.
    visible = lambda row: {key: value for key, value in row.items() if key != 'scoring'}
    changed_rows = {identifier for identifier in rows
                    if visible(old_rows[identifier]) != visible(rows[identifier])
                    or rows[identifier]['case_id'] in changed_cases}
    return changed_rows, changed_cases


def _presentation_detail_scope(records, selected, manifest, renderer_sha256):
    """A changed presentation template invalidates every saved result page."""
    previous = manifest.get('presentation_refresh', {}).get('renderer_sha256')
    return ({row['attempt_id'] for row in records}
            if previous != renderer_sha256 else set(selected))


def _current_template_refresh(output, binding, manifest):
    """Select all current pages only after their authority has been validated."""
    current = file_sha256(Path(__file__).with_name('report.py'))
    if manifest.get('presentation_refresh', {}).get('renderer_sha256') == current:
        return False
    revision = binding.get('offline_revision')
    if not revision:
        return False
    from .publication_privacy import read_evidence_binding
    authority = json.loads(read_evidence_binding(output, revision).read_text())
    return (authority.get('kind') == 'current_local_snapshot_authority'
            and authority.get('schema_version') == 1)


def refresh_local_presentation(output: Path, binding_path: Path, *, review_only: bool = False) -> Path:
    """Install explanatory routes and, on request, bound fresh review details."""
    from .safety import unlinked_path, evidence_tree_identity, copy_evidence_file
    output = unlinked_path(output)
    before_tree = evidence_tree_identity(output)
    binding, dataset, records, manifest = validate_local_presentation(output, binding_path)
    detail_attempt_ids = None
    dataset_case_ids = set()
    revised_attempt_ids = set()
    refresh_all_current_pages = False
    if review_only:
        from .adjudication_evidence import require_adjudication_evidence, require_current_review_state
        review_path = contained_path(output, 'result-review.json', reject_symlinks=True)
        if not any(item.get('path') == 'result-review.json'
                   and item.get('sha256') == file_sha256(review_path)
                   for item in binding.get('evidence_bindings', [])):
            raise ValueError('Review refresh requires a hash-bound result review')
        review = json.loads(review_path.read_text())
        require_current_review_state(records, review)
        require_adjudication_evidence(output, dataset, records, review)
        detail_attempt_ids = {row['attempt_id'] for row in review['attempts']
                              if row.get('review_origin') == 'fresh'}
        if not detail_attempt_ids:
            raise ValueError('Review refresh requires supplemental fresh reviews')
        revised_attempt_ids, dataset_case_ids = _review_refresh_changes(output, binding, dataset, records)
        detail_attempt_ids |= revised_attempt_ids
        detail_attempt_ids = _presentation_detail_scope(
            records, detail_attempt_ids, manifest,
            file_sha256(Path(__file__).with_name('report.py')))
        refresh_all_current_pages = _current_template_refresh(output, binding, manifest)
        if refresh_all_current_pages:
            dataset_case_ids = {case['id'] for case in dataset['cases']}
    authored_interpretation = manifest.get('presentation', {}).get(
        'authored_interpretation', bool(dataset.get('comparison', {}).get('report_interpretation')))
    # The existing owner renders from saved metrics. Only these explanatory
    # routes may be installed; review mode also selects fresh result details.
    # Logs and every measured byte remain outside the install allowlist.
    routes = ['assets/report.js', 'assets/style.css', 'index.html', 'methodology/index.html', 'adoption/index.html', 'reproduce/index.html', 'changes/index.html']
    routes += ['providers/' + provider + '.html' for provider in binding['providers']]
    routes += ['adoption/interfaces/' + provider + '.json' for provider in binding['providers']]
    if review_only:
        routes += ['review/index.html']
        if refresh_all_current_pages:
            routes += ['guide/index.html']
            current_article = (authored_interpretation
                and not dataset.get('comparison', {}).get('call_chain_audit')
                and dataset.get('comparison', {}).get('report_interpretation'))
            if current_article:
                routes += ['guide/article.html', 'guide/interpretation-evidence.json']
                if (output/'guide/cowagent-text-probe.json').is_file():
                    routes += ['guide/cowagent-text-probe.json']
        routes += ['attempts/' + identifier + '.html' for identifier in sorted(detail_attempt_ids)]
        if revised_attempt_ids or dataset_case_ids:
            routes += ['analysis/index.html', 'summary.json', 'reliability.json']
        if dataset_case_ids:
            routes += ['datasets/index.html']
            for identifier in sorted(dataset_case_ids):
                routes += ['datasets/' + identifier + '.html', 'references/' + identifier + '.txt']
                if next(case for case in dataset['cases'] if case['id'] == identifier).get('acceptance_contract'):
                    routes += ['contracts/' + identifier + '.json']
    diagnostic_view_paths = sorted(path.relative_to(output).as_posix()
        for path in (output/'logs').rglob('*.html')
        if path.with_suffix('.txt').is_file()) if review_only else []
    routes += diagnostic_view_paths
    include_english = 'en' in manifest['languages']
    if include_english:
        routes += ['en/' + relative for relative in routes if relative.endswith('.html')]
    with tempfile.TemporaryDirectory(prefix='.presentation-', dir=output.parent) as scratch:
        staging = Path(scratch) / 'report'
        shutil.copytree(output, staging, copy_function=copy_evidence_file)
        batches = json.loads((output / 'execution.json').read_text())['batches']
        generate(staging, dataset, records, batches, include_english=include_english, presentation_only=True,
                 frozen_scores=True, authored_interpretation=authored_interpretation,
                 detail_attempt_ids=detail_attempt_ids,
                 diagnostic_view_paths=diagnostic_view_paths,
                 dataset_case_ids=dataset_case_ids,
                 english_routes=[r for r in routes if r.endswith('.html') and not r.startswith('en/')])
        if revised_attempt_ids or dataset_case_ids:
            write_frozen_result_summary(staging, dataset, records)
        changed = []
        for relative in routes:
            before, after = contained_path(output, relative), contained_path(staging, relative)
            if not after.is_file() or (not review_only and not before.is_file()):
                raise ValueError('Presentation route missing: ' + relative)
            if not before.is_file() or before.read_bytes() != after.read_bytes():
                changed.append(relative)
        manifest['presentation_refresh'] = {
            'provider_calls_added': 0, 'scores_changed': False,
            'scoring_git_revision': binding.get('measured_scoring_git_revision'),
            'scoring_source_sha256': binding['measured_scoring_source_sha256'],
            'renderer_sha256': file_sha256(Path(__file__).with_name('report.py')),
            'changed_routes': sorted(set(changed) | set(manifest.get('presentation_refresh', {}).get('changed_routes', []))),
            **({'revised_attempt_ids': sorted(revised_attempt_ids),
                'revised_case_ids': sorted(dataset_case_ids),
                'refresh_baseline': binding['review_refresh_baseline']}
               if binding.get('review_refresh_baseline') else {}),
        }
        # Stage the permitted pages into a copy of the original snapshot so no
        # renderer side effects can change evidence or unrelated routes.
        selected = Path(scratch) / 'selected'
        shutil.copytree(output, selected, copy_function=copy_evidence_file)
        for relative in changed:
            shutil.copy2(staging / relative, selected / relative)
        if review_only:
            shutil.copy2(binding_path, selected / 'presentation-binding.json')
        write_frozen_presentation_manifest(selected, manifest,
            show_local_status_banners=manifest.get('presentation', {}).get('show_local_status_banners', True),
            authored_interpretation=authored_interpretation)
        if evidence_tree_identity(output) != before_tree:
            raise ValueError('Presentation snapshot changed during rendering')
        _install_report_tree(selected, output)
    return output / 'index.html'
