"""Verify public definitions and materialize independently acquired frozen inputs."""
from __future__ import annotations

import hashlib
from copy import deepcopy
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from .paths import contained_path, safe_identifier
from .inputs import digest, load_manifest, verify_catalog
from .annotation_revisions import apply_annotation_change

from harness.benchmarks.data import binding as dataset_binding, cache_home, definition_path

SUITES = cache_home() / "webfetch" / dataset_binding("webfetch")["revision"] / "definitions"

# Full reference bodies and source-capture/review payloads stay in frozen inputs.
PUBLIC_TASK_FIELDS = (
    "id", "name", "url", "category", "track", "task_kind", "description", "task_prompt",
    "expected_any", "required_facts", "forbidden_any", "diagnostic_any", "min_content_chars",
    "fact_coverage_threshold", "expected_resources", "resource_output_contract",
    "content_score_applicable", "required_patterns", "acceptance_contract",
    "composite_acceptance", "mutable_body_policy", "body_requirements", "retrieval_hints",
    "document_structure",
    "source_review_url", "evaluation_url", "expected_final_url", "page_identity_required",
    "expected_document_language", "document_language_match",
    "expected_document_language_present", "target_language", "target_region",
    "ordered_tree_min_similarity", "ordered_tree_reference_sha256",
)


def reference_bindings(value):
    """Keep body-rule structure while binding external reference payloads."""
    if isinstance(value, list):
        return [reference_bindings(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"text", "source_text"}:
            if not isinstance(item, str):
                raise ValueError("Body reference text must be a string")
            result[key + "_binding"] = {
                "sha256": hashlib.sha256(item.encode()).hexdigest(), "chars": len(item)}
        else:
            result[key] = reference_bindings(item)
    return result


def public_task(case: dict) -> dict:
    task = {key: case[key] for key in PUBLIC_TASK_FIELDS if key in case}
    if task.get('ordered_tree_min_similarity') is None:
        task.pop('ordered_tree_min_similarity', None)
    if "body_requirements" in task:
        task["body_requirements"] = reference_bindings(task["body_requirements"])
    if case.get("ordered_tree_reference") is not None:
        task["ordered_tree_reference_sha256"] = hashlib.sha256(json.dumps(
            case["ordered_tree_reference"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    benchmark = case.get("source_benchmark", {})
    routing = {key: benchmark[key] for key in
               ("mode", "hosts", "entry_path", "minimum_entries", "verification_scope",
                "entry_identity_pattern", "identity_query_keys", "link_base_url", "entry_scope",
                "name_validation", "record_name_exclusions", "title_exclusion_patterns",
                "price_patterns", "price_exclusion_patterns", "price_unavailable_patterns")
               if key in benchmark}
    from .required_components import COMPONENTS
    for component in COMPONENTS:
        if component in benchmark:
            routing[component] = benchmark[component]
    navigation = benchmark.get("risk_control", {}).get("navigation_contract")
    if navigation is not None:
        routing["risk_control"] = {"navigation_contract": navigation}
    if routing:
        task["source_benchmark"] = routing
    verification = {key: case.get("source_verification", {})[key]
                    for key in ("mime", "file_sha256", "reference_mode")
                    if key in case.get("source_verification", {})}
    if verification:
        task["source_verification"] = verification
    review = case.get("rule_review", {})
    if review:
        task["rule_review"] = {key: review[key] for key in ("status", "reason") if key in review}
    return task






def read(path: Path) -> dict:
    try:
        relative = path.relative_to(cache_home() / "webfetch")
    except ValueError:
        pass
    else:
        if len(relative.parts) >= 3 and relative.parts[1] == "definitions":
            path = definition_path("webfetch", Path(*relative.parts[2:]).as_posix(),
                                   revision=relative.parts[0])
    return json.loads(path.read_text(encoding="utf-8"))


def registry() -> dict:
    document = read(SUITES / "registry.json")
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported suite registry")
    ids = [row["id"] for row in document["suites"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate suite ID")
    return document


def suite(identifier: str) -> tuple[dict, Path]:
    matches = [row for row in registry()["suites"] if row["id"] == identifier]
    if len(matches) != 1:
        raise ValueError(f"Unknown suite: {identifier}")
    entry = matches[0]
    root = contained_path(SUITES, entry.get("directory", identifier), reject_symlinks=True)
    for relative, expected in entry["files"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid SHA-256: {relative}")
        if digest(contained_path(root, relative, reject_symlinks=True)) != expected:
            raise ValueError(f"Changed suite file: {identifier}/{relative}")
    return entry, root


def validate(identifier: str) -> dict:
    entry, root = suite(identifier)
    catalog = read(root / "dataset-catalog.json")
    tasks = read(root / "tasks.json")
    manifest = load_manifest(root / "source-inputs.json")
    if manifest['catalog'] != 'dataset-catalog.json':
        raise ValueError('Manifest must select the registry-bound task catalog')
    if not (entry["dataset_id"] == catalog["id"] == tasks["id"]):
        raise ValueError("Inconsistent dataset identity")
    declared = catalog["cases"]
    cases = tasks["cases"]
    ids = [case["id"] for case in cases]
    if (len(ids) != len(set(ids)) or ids != [case["id"] for case in declared]
            or len(ids) != entry["case_count"] or len(ids) != catalog["case_count"]):
        raise ValueError("Inconsistent task inventory")
    for task, row in zip(cases, declared):
        for key in ("id", "name", "url", "category", "track", "task_prompt"):
            if key in row and row[key] != task.get(key):
                raise ValueError(f"Task catalog mismatch: {task['id']}/{key}")
    for relative, expected in input_files(manifest).items():
        contained_path(root, relative, reject_symlinks=True)
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid input SHA-256: {relative}")
    for path in sorted((SUITES/'annotation-revisions').glob('*.json')):
        revision = read(path)
        if identifier in revision.get('parents', {}):
            apply_annotation_revision(identifier, tasks, revision['id'])
    return {"suite": identifier, "case_count": len(ids), "dataset_sha256": catalog["dataset_sha256"]}


def input_files(manifest: dict) -> dict[str, str]:
    files = dict(manifest["files"])
    for tree, items in manifest["trees"].items():
        for relative, expected in items.items():
            name = f"{tree}/{relative}"
            if name in files:
                raise ValueError(f"Duplicate input: {name}")
            files[name] = expected
    return files


def verify_inputs(identifier: str, source: Path) -> dict:
    result = validate(identifier)
    _, root = suite(identifier)
    manifest = read(root / "source-inputs.json")
    for relative, expected in input_files(manifest).items():
        if digest(contained_path(source, relative, reject_symlinks=True)) != expected:
            raise ValueError(f"Changed frozen input: {relative}")
    raw = contained_path(source, manifest['dataset'], reject_symlinks=True).read_bytes()
    verify_catalog(root/'source-inputs.json', manifest, raw)
    dataset = json.loads(raw)
    tasks = read(root/'tasks.json')['cases']
    for case, task in zip(dataset['cases'], tasks):
        if public_task(case) != task:
            raise ValueError(f"Public task differs from frozen input: {case['id']}")
    return result


def annotation_changes(before, after, path=''):
    """Describe changed leaves; lists retain their order and exact values."""
    if isinstance(before, dict) and isinstance(after, dict):
        rows = []
        for key in sorted(before.keys() | after.keys()):
            child = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in before: rows.append({'path': child, 'after': after[key]})
            elif key not in after: rows.append({'path': child, 'before': before[key]})
            else: rows.extend(annotation_changes(before[key], after[key], child))
        return rows
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [row for index, (old, new) in enumerate(zip(before, after))
                for row in annotation_changes(old, new, path + '/' + str(index))]
    return [] if before == after else [{'path': path, 'before': before, 'after': after}]


def review_input_files(definition, identifier):
    """Bind additional review evidence separately from the immutable parent inputs."""
    manifests = definition.get('review_inputs', {})
    if (not isinstance(manifests, dict) or set(manifests) - set(definition['parents'])
            or any(not isinstance(files, dict) for files in manifests.values())):
        raise ValueError('Invalid revision review-input manifest')
    for files in manifests.values():
        for relative, sha256 in files.items():
            contained_path(SUITES, relative)
            if (not isinstance(relative, str) or not relative.startswith('review-evidence/')
                    or Path(relative).as_posix() != relative
                    or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256)):
                raise ValueError('Review evidence requires a namespaced path and SHA-256')
    return manifests.get(identifier, {})


def _review_annotation(definition, revision_path, identifier, root, changes):
    from .scoring_regressions import check_scoring_regressions
    reviews_path = contained_path(SUITES, 'annotation-revisions/reviews.json', reject_symlinks=True)
    if not reviews_path.is_file():
        raise ValueError('Annotation revision requires per-task evidence and controls')
    reviews = read(reviews_path)
    review = reviews.get('revisions', {}).get(definition['id'], {})
    if reviews.get('schema_version') != 1 or review.get('revision_sha256') != digest(revision_path):
        raise ValueError('Annotation review differs from the declared revision')
    shared_reasons = review.get('shared_reasons', {})
    if (not isinstance(shared_reasons, dict)
            or any(not isinstance(key, str) or not key.strip()
                   or not isinstance(value, str) or not value.strip()
                   for key, value in shared_reasons.items())):
        raise ValueError('Invalid shared annotation reasons')
    entries = review.get('changes', [])
    keys = [(item['suite'], item['case_id']) for item in entries]
    if len(keys) != len(set(keys)) or set(keys) != {(item['suite'], item['case_id']) for item in definition['changes']}:
        raise ValueError('Annotation review task inventory differs')
    declared_inputs = input_files(read(root/'source-inputs.json'))
    review_inputs = review_input_files(definition, identifier)
    if set(declared_inputs) & set(review_inputs):
        raise ValueError('Review evidence cannot replace parent inputs')
    declared_inputs.update(review_inputs)
    records = []
    for change, old, new in changes:
        item = entries[keys.index((identifier, change['case_id']))]
        delta = annotation_changes(public_task(old), public_task(new))
        encoded = json.dumps(delta, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if item.get('changes_sha256') != hashlib.sha256(encoded).hexdigest():
            raise ValueError('Annotation includes unreviewed field changes: ' + change['case_id'])
        reason = item.get('reason')
        if 'reason_ref' in item:
            reference = item['reason_ref']
            if ('reason' in item or not isinstance(reference, str)
                    or reference not in shared_reasons):
                raise ValueError('Invalid annotation reason reference')
            reason = shared_reasons[reference]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Annotation requires a per-task reason')
        sources = item.get('sources', [])
        if not sources or any(source.get('path') not in declared_inputs
                              or declared_inputs[source['path']] != source.get('sha256')
                              or not isinstance(source.get('scope'), str) or not source['scope'].strip()
                              for source in sources):
            raise ValueError('Annotation source is not bound by its frozen inputs')
        controls = item.get('regression_ids', [])
        if not controls or set(controls) - set(definition['regression_ids']):
            raise ValueError('Annotation controls differ from its declared revision')
        check_scoring_regressions(controls)
        records.append({'suite': identifier, 'case_id': change['case_id'],
                        'reason': reason, 'sources': sources, 'changes': delta,
                        'regression_ids': controls,
                        'source_coverage': ('revision_review_source_declared' if any(source['path'] in review_inputs for source in sources)
                                            else 'reference_source_declared' if any(source['path'] != 'dataset.json' for source in sources)
                                            else 'task_and_source_hashes_only')})
    return {'revision': definition['id'], 'revision_sha256': digest(revision_path),
            'review_sha256': digest(reviews_path), 'changes': records,
            **({'review_inputs': review_inputs} if review_inputs else {})}


def apply_annotation_revision(identifier: str, dataset: dict, revision: str) -> dict:
    """Apply a reviewed score-only version without changing its frozen parent."""
    return _apply_annotation_revision(identifier, dataset, revision)[0]


def inspect_annotation_revision(identifier: str, revision: str) -> dict:
    """Inspect exact field differences, frozen sources and passing controls."""
    _, root = suite(identifier)
    return _apply_annotation_revision(identifier, read(root/'tasks.json'), revision)[1]


def _apply_annotation_revision(identifier: str, dataset: dict, revision: str):
    safe_identifier(revision)
    path = contained_path(SUITES, f'annotation-revisions/{revision}.json', reject_symlinks=True)
    definition = read(path)
    if (definition.get('schema_version') != 1
            or definition.get('kind') != 'score_only_annotation_revision'
            or definition.get('id') != revision):
        raise ValueError('Unsupported annotation revision')
    from .scoring_regressions import load_scoring_regressions
    controls = definition.get('regression_ids', [])
    if not controls or set(controls) - load_scoring_regressions().keys():
        raise ValueError('Annotation revision requires registered regression controls')
    entry, root = suite(identifier)
    parent = definition.get('parents', {}).get(identifier)
    if parent != {'tasks_sha256': digest(root/'tasks.json'),
                  'source_inputs_sha256': digest(root/'source-inputs.json')}:
        raise ValueError('Annotation revision has a different frozen parent')
    if dataset.get('id') != entry['dataset_id']:
        raise ValueError('Annotation revision dataset identity differs')
    result = deepcopy(dataset)
    cases = {case['id']: case for case in result['cases']}
    shared_contracts = definition.get('shared_acceptance_contracts', {})
    if not isinstance(shared_contracts, dict):
        raise ValueError('Invalid shared acceptance contracts')
    from .stable_tasks import validate_acceptance_contract
    for name, contract in shared_contracts.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Invalid shared acceptance contract name')
        validate_acceptance_contract(contract)
    changes = [row for row in definition['changes'] if row['suite'] == identifier]
    if not changes or len({row['case_id'] for row in changes}) != len(changes):
        raise ValueError('Annotation revision has an invalid task inventory')
    reviewed_changes = []
    reference_hashes = {row['id']: row.get('reference_sha256')
                        for row in read(root / 'dataset-catalog.json')['cases']}
    for change in changes:
        case = cases.get(change['case_id'])
        public = public_task(case) if case is not None else None
        encoded = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(encoded).hexdigest() != change['before_sha256']:
            raise ValueError('Annotation revision task differs from frozen parent')
        old = deepcopy(case)
        effective_change = change
        if 'acceptance_contract_ref' in change:
            reference = change['acceptance_contract_ref']
            if (not isinstance(reference, str) or reference not in shared_contracts
                    or 'acceptance_patch' in change
                    or 'acceptance_contract' in change.get('set', {})
                    or 'acceptance_contract' in change.get('remove', [])):
                raise ValueError('Invalid or conflicting shared acceptance reference')
            effective_change = deepcopy(change)
            effective_change.setdefault('set', {})['acceptance_contract'] = shared_contracts[reference]
        apply_annotation_change(case, effective_change, reference_hashes[case['id']])
        reviewed_changes.append((change, old, case))
    review = _review_annotation(definition, path, identifier, root, reviewed_changes)
    result['id'] = dataset['id'] + '--' + revision
    result['definition_revision'] = {
        'id': revision, 'sha256': digest(path), 'parent_dataset_id': dataset['id'],
        'parent_tasks_sha256': parent['tasks_sha256'], 'provider_calls_added': 0,
    }
    return result, review


def _body_reference_additions(changes):
    """Recognize body-only edits plus strictly appended stable-text requirements."""
    if not changes:
        return None
    added = []
    for change in changes:
        if change['path'].startswith('/body_requirements/'):
            continue
        if change['path'] != '/acceptance_contract/stable_text_anchors':
            return None
        before, after = change.get('before'), change.get('after')
        if (not isinstance(before, list) or not isinstance(after, list)
                or len(after) <= len(before) or after[:len(before)] != before):
            return None
        added.extend(after[len(before):])
    return added


def check_revision_references(dataset: dict, review: dict, source: Path, *,
                              include_review_evidence: bool = True) -> dict:
    """Run frozen reference and error-page controls without treating absent text as a pass."""
    from html import escape
    from .scoring import score_attempt, SCORING_SOURCE_SHA256
    from .metrics import task_passed
    from .body_coverage import evaluate_body_requirements
    from .stable_tasks import evaluate_stable_task
    cases = {case['id']: case for case in dataset['cases']}
    results = []
    for item in review['changes']:
        case = cases[item['case_id']]
        added_anchors = _body_reference_additions(item.get('changes'))
        candidates = []
        unavailable_sources = []
        for field in ('reference_answer', 'ground_truth_main_content'):
            if isinstance(case.get(field), str) and case[field].strip():
                candidates.append((case[field].encode(), {'path': 'dataset.json', 'field': field}))
        for binding in item['sources']:
            if binding['path'] == 'dataset.json':
                continue
            if not include_review_evidence and binding['path'] in review.get('review_inputs', {}):
                unavailable_sources.append(binding['path'])
                continue
            path = contained_path(source, binding['path'], reject_symlinks=True)
            if digest(path) != binding['sha256']:
                raise ValueError('Reference control source hash mismatch: ' + binding['path'])
            raw = path.read_bytes()
            if path.suffix == '.json':
                payload = json.loads(raw)
                resources = payload.get('resource_candidates') if isinstance(payload, dict) else None
                if isinstance(resources, list):
                    image_answer = bool(case.get('expected_resources')) and all(
                        resource.get('kind') == 'image' for resource in case['expected_resources'])
                    lines = []
                    for resource in resources:
                        url = resource.get('url')
                        if isinstance(url, str):
                            lines.append(('<img src="' if image_answer else '<a href="')
                                         + escape(url, quote=True) + ('">' if image_answer else '">Reference</a>'))
                    raw = '\n'.join(lines).encode()
                elif isinstance(payload, dict) and isinstance(payload.get('reference'), str):
                    raw = payload['reference'].encode()
                else:
                    continue
            candidates.append((raw, {'path': binding['path'], 'sha256': binding['sha256']}))
        controls = []
        for raw, binding in candidates:
            # An explicitly bound main body does not contain every page header,
            # title or comment. It can qualify a body-only change, not a whole
            # page contract. Other annotation edits still need a full-task pass.
            scoped_body = (added_anchors is not None and isinstance(case.get('body_requirements'), dict)
                           and binding.get('field') == 'ground_truth_main_content')
            if scoped_body:
                passed = evaluate_body_requirements(raw.decode(), case['body_requirements'])['body_coverage_pass']
                if added_anchors:
                    added = evaluate_stable_task(raw.decode(), {
                        'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                        'stable_text_anchors': added_anchors}, case.get('url', ''))
                    passed = passed and added['stable_task_pass']
                scope = 'body_and_added_stable_text' if added_anchors else 'body_only'
            else:
                metrics = score_attempt(case, raw, ok=True)
                passed = task_passed(True, metrics, case.get('task_kind', 'body'))
                scope = 'full_task'
            controls.append({'source': binding, 'answer_sha256': hashlib.sha256(raw).hexdigest(),
                             'scope': scope, 'passed': passed})
            if passed and scope == 'full_task':
                break
        negative = b'<html><body>Verify you are human. Access Denied. CAPTCHA.</body></html>'
        metrics = score_attempt(case, negative, ok=True)
        negative_pass = task_passed(True, metrics, case.get('task_kind', 'body'))
        status = ('failed' if negative_pass
                  else 'passed' if any(control['passed'] for control in controls)
                  else 'unavailable' if unavailable_sources
                  else 'failed' if controls else 'unavailable')
        results.append({'case_id': case['id'], 'status': status, 'reference_candidates': controls,
                        'verification_scope': next((c['scope'] for c in reversed(controls) if c['passed']), 'unavailable'),
                        'error_page_passed': negative_pass,
                        **({'unavailable_review_sources': unavailable_sources} if unavailable_sources else {}),
                        'reason': ('Optional review evidence was not supplied; existing control results are retained.'
                                   if unavailable_sources else
                                   'The frozen inputs retain source hashes but no executable reference answer.'
                                   if not controls else '')})
    return {'scoring_source_sha256': SCORING_SOURCE_SHA256,
            'complete': all(result['status'] == 'passed' and result['verification_scope'] == 'full_task'
                            for result in results),
            'changed_rules_complete': all(result['status'] == 'passed' for result in results),
            'cases': results}


def export(identifier: str, source: Path, output: Path, *, revision: str | None = None,
           review_source: Path | None = None) -> Path:
    """Copy only declared inputs after verification; never overwrite a run or input tree."""
    if review_source is not None and revision is None:
        raise ValueError('Review evidence requires an annotation revision')
    verify_inputs(identifier, source)
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory")
    if not output.parent.is_dir():
        raise ValueError("Output parent must exist")
    _, root = suite(identifier)
    manifest = read(root / "source-inputs.json")
    stage = Path(tempfile.mkdtemp(prefix=".lexbench-", dir=output.parent))
    try:
        for relative in input_files(manifest):
            target = contained_path(stage, relative, reject_symlinks=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(contained_path(source, relative, reject_symlinks=True), target)
        verify_inputs(identifier, stage)
        if revision is not None:
            dataset_path = stage / manifest['dataset']
            revised, review = _apply_annotation_revision(identifier, read(dataset_path), revision)
            extra_inputs = review.get('review_inputs', {})
            for relative, sha256 in (extra_inputs.items() if review_source is not None else ()):
                original = contained_path(review_source, relative, reject_symlinks=True)
                if not original.is_file() or digest(original) != sha256:
                    raise ValueError('Review evidence is missing or changed: ' + relative)
                target = contained_path(stage, relative, reject_symlinks=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                if digest(target) != sha256:
                    raise ValueError('Review evidence changed during export: ' + relative)
            review['reference_controls'] = check_revision_references(
                revised, review, stage, include_review_evidence=review_source is not None)
            if any(item['status'] == 'failed' for item in review['reference_controls']['cases']):
                raise ValueError('Revised contract rejects its frozen reference or accepts an error page')
            (stage/'annotation-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            dataset_path.write_text(json.dumps(revised, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # A materialized dataset is intentionally independent of the historical
        # Browser Eval external-input resolver; do not copy source-inputs.json.
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return output / manifest["dataset"]
