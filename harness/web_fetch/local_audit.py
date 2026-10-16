"""Fail-closed validation for explicitly non-publishable local audit bundles."""
import hashlib
import json
from pathlib import Path

from .metrics import task_passed
from .reference_inputs import input_tree, read_dataset_bytes
from .safety import contained_path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "local-audit-manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _repository_file(relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Repository evidence path is missing")
    path = contained_path(ROOT, relative)
    cursor = ROOT
    has_symlink = False
    for part in Path(relative).parts:
        cursor = cursor / part
        has_symlink = has_symlink or cursor.is_symlink()
    if not path.is_file() or has_symlink:
        raise ValueError("Repository evidence is missing or unsafe: " + relative)
    return path


def _source_evidence_inventory(dataset: dict) -> list[dict]:
    purposes = {
        "evidence.json": "source verification receipt",
        "reference.txt": "frozen scoring reference",
        "source.html.txt": "captured source response",
        "current-source.html.txt": "current source response",
        "current-capture.json": "current capture receipt",
        "mixed-session-stable-text.json": "cross-session stability receipt",
        "rendered.txt": "independently rendered visible text",
        "render-audit.json": "render audit receipt",
        "initial.html.txt": "initial HTTP response",
        "stable-page.txt": "stable page observation",
        "stable-page.json": "stable page receipt",
        "source.pdf": "independent source document",
        "items.json": "independent structured source items",
        "current-browser-observation.json": "current browser observation",
        "source.bin": "independent binary source",
        "browser-observation.json": "browser observation receipt",
        "unavailable-reference.json": "reference unavailability receipt",
        "source.docx": "independent source document",
        "body-reference.json": "body reference receipt",
    }
    rows = []
    source_root = input_tree(ROOT / "evaluation/dataset.json", "sources")
    if source_root.is_symlink():
        raise ValueError("Repository source evidence root must not be a symlink")
    for case_id in sorted(case["id"] for case in dataset.get("cases", [])):
        raw_directory = source_root / case_id
        if raw_directory.is_symlink():
            raise ValueError("Repository source evidence must not contain symlinks")
        directory = contained_path(source_root, case_id)
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("Repository source evidence must not contain symlinks")
            if not path.is_file():
                continue
            rows.append({
                "case_id": case_id,
                "path": "evaluation/sources/" + path.relative_to(source_root).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "purpose": purposes.get(path.name, "independent source evidence"),
            })
    return rows


def _require_repository_bindings(dataset: dict, receipt: dict) -> None:
    """Verify committed source evidence and scoring controls as exact inventories."""
    bindings = receipt.get("repository_evidence", {})
    source_path = _repository_file(bindings.get("source_manifest_path"))
    if _sha256(source_path) != bindings.get("source_manifest_sha256"):
        raise ValueError("Repository source evidence manifest changed")
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    if (source_manifest.get("schema_version") != 1
            or source_manifest.get("kind") != "dataset_source_evidence_inventory"
            or source_manifest.get("dataset_id") != dataset.get("id")
            or source_manifest.get("dataset_sha256")
            != receipt.get("bindings", {}).get("dataset_sha256")):
        raise ValueError("Repository source evidence manifest has wrong bindings")
    declared_files = source_manifest.get("files", [])
    if declared_files != _source_evidence_inventory(dataset):
        raise ValueError("Repository source evidence inventory or hashes changed")
    scope = source_manifest.get("scope", {})
    if (scope.get("dataset_cases") != len(dataset.get("cases", []))
            or scope.get("cases_with_source_evidence")
            != len({row["case_id"] for row in declared_files})
            or scope.get("files") != len(declared_files)):
        raise ValueError("Repository source evidence scope is inconsistent")

    scoring_path = _repository_file(bindings.get("scoring_revision_path"))
    if _sha256(scoring_path) != bindings.get("scoring_revision_sha256"):
        raise ValueError("Scoring revision receipt changed")
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    if (scoring.get("schema_version") != 1
            or scoring.get("kind") != "scoring_revision_receipt"
            or scoring.get("dataset_id") != dataset.get("id")
            or scoring.get("dataset_sha256")
            != receipt.get("bindings", {}).get("dataset_sha256")
            or scoring.get("provider_calls") != 0
            or scoring.get("bindings", {}).get("scoring_source_sha256")
            != receipt.get("bindings", {}).get("scoring_source_sha256")):
        raise ValueError("Scoring revision receipt has wrong bindings")
    regressions_path = _repository_file("evaluation/scoring-regressions.json")
    # The historical receipt's whole-file hash predates appended controls.
    # Verify its exact ordered prefix and canonical contents below. Canonical
    # hashes alone cannot detect interleaving the two historical partitions.
    # Later scorer revisions bind the full registry; controls are append-only.
    controls = json.loads(regressions_path.read_text(encoding="utf-8")).get("cases", [])
    current_ids = [row.get("id") for row in controls]
    base_ids = scoring.get("preexisting_control_ids", [])
    revision_rows = scoring.get("revision_controls", [])
    revision_ids = [row.get("id") for row in revision_rows]
    bound_count = len(base_ids) + len(revision_ids)
    # A newer revision may append controls, but cannot replace or interleave
    # any control that the historical audit bound by the hashes below.
    bound_ids = current_ids[:bound_count]
    if (len(current_ids) != len(set(current_ids))
            or len(base_ids) != len(set(base_ids))
            or len(revision_ids) != len(set(revision_ids))
            or set(base_ids) & set(revision_ids)
            or bound_ids != base_ids + revision_ids):
        raise ValueError("Scoring control partition is incomplete or duplicated")
    base_set, revision_set = set(base_ids), set(revision_ids)
    base_controls = [row for row in controls if row["id"] in base_set]
    revision_controls = [row for row in controls if row["id"] in revision_set]
    if (_canonical_sha256(base_controls)
            != scoring.get("bindings", {}).get("preexisting_controls_sha256")
            or _canonical_sha256(revision_controls)
            != scoring.get("bindings", {}).get("revision_controls_sha256")):
        raise ValueError("Scoring control contents changed")
    expected_rows = [{
        "id": row["id"], "root_cause": row.get("root_cause"),
        "failure_mode": row.get("failure_mode"),
    } for row in revision_controls]
    if revision_rows != expected_rows:
        raise ValueError("Scoring revision control metadata changed")
    root_causes = {}
    for row in revision_controls:
        root = root_causes.setdefault(
            row.get("root_cause"), {"false-positive": [], "false-negative": []})
        if row.get("failure_mode") not in root:
            raise ValueError("Scoring control has an unsupported failure mode")
        root[row["failure_mode"]].append(row["id"])
    expected_roots = [{
        "id": key,
        "false_positive_ids": sorted(value["false-positive"]),
        "false_negative_ids": sorted(value["false-negative"]),
    } for key, value in sorted(root_causes.items())]
    if (scoring.get("root_causes") != expected_roots
            or any(not row["false_positive_ids"] or not row["false_negative_ids"]
                   for row in expected_roots)):
        raise ValueError("Scoring root causes lack paired error-direction controls")
    scope = scoring.get("scope", {})
    if (scope.get("preexisting_controls") != len(base_controls)
            or scope.get("revision_controls") != len(revision_controls)
            or scope.get("root_causes") != len(expected_roots)
            or scope.get("paired_error_directions") is not True):
        raise ValueError("Scoring revision scope is inconsistent")


def audit_file_inventory(source: Path) -> list[dict]:
    """Return the complete regular-file inventory, rejecting every symlink."""
    source = source.resolve()
    if source.is_symlink():
        raise ValueError("Local audit root must not be a symlink")
    rows = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Local audit evidence must not contain symlinks")
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        rows.append({
            "path": path.relative_to(source).as_posix(),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        })
    return rows


def local_audit_manifest(source: Path) -> dict:
    """Build the manifest document used by the local-audit boundary."""
    source = source.resolve()
    bindings = {}
    for name in ("dataset.json", "results.jsonl", "result-review.json"):
        path = contained_path(source, name)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Local audit required file is missing or unsafe: " + name)
        bindings[name] = _sha256(path)
    return {
        "schema_version": 1,
        "kind": "complete_local_audit_inventory",
        "bindings": bindings,
        "files": audit_file_inventory(source),
    }


def _require_manifest(source: Path, receipt: dict) -> dict:
    evidence = receipt.get("evidence", {})
    relative = evidence.get("manifest_path")
    expected = evidence.get("manifest_sha256")
    if relative != MANIFEST_NAME or not expected:
        raise ValueError("Local audit receipt does not bind the complete manifest")
    path = contained_path(source, relative)
    if not path.is_file() or path.is_symlink() or _sha256(path) != expected:
        raise ValueError("Local audit manifest is missing, unsafe, or changed")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported local audit manifest schema")
    declared = manifest.get("files", [])
    paths = [row.get("path") for row in declared]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Local audit manifest paths must be complete and unique")
    actual = audit_file_inventory(source)
    if declared != actual:
        raise ValueError("Local audit file inventory or hashes changed")
    return manifest


def _require_review(source: Path, dataset: dict, records: list[dict],
                    receipt: dict, manifest: dict) -> dict:
    path = contained_path(source, "result-review.json")
    expected_review = receipt.get("evidence", {}).get("result_review_sha256")
    if not expected_review or _sha256(path) != expected_review:
        raise ValueError("Local audit result review changed")
    review = json.loads(path.read_text(encoding="utf-8"))
    if review.get("schema_version") != 1:
        raise ValueError("Unsupported local audit review schema")
    protocol = review.get("review_protocol", {})
    if protocol.get("scope") != "supplement-25-local-audit":
        raise ValueError("Unsupported local audit review scope")
    bindings = review.get("bindings", {})
    for name, key in (("dataset.json", "dataset_sha256"),
                      ("results.jsonl", "results_sha256")):
        expected = manifest.get("bindings", {}).get(name)
        if bindings.get(key) != expected:
            raise ValueError("Local audit review is bound to different final inputs")
    review_contract = receipt.get("review", {})
    for key in ("supplement_dataset_sha256", "supplement_results_sha256"):
        if bindings.get(key) != review_contract.get(key):
            raise ValueError("Local audit supplement review lineage changed: " + key)
    attempts = review.get("attempts", [])
    ids = [row.get("attempt_id") for row in attempts]
    expected_count = review_contract.get("supplement_cases_reviewed")
    if len(ids) != len(set(ids)) or len(ids) != expected_count:
        raise ValueError("Local audit supplement review coverage changed")
    by_id = {row["attempt_id"]: row for row in records}
    cases = {case["id"]: case for case in dataset.get("cases", [])}
    if not set(ids).issubset(by_id):
        raise ValueError("Local audit review names unknown attempts")
    for row in attempts:
        record = by_id[row["attempt_id"]]
        machine = "pass" if task_passed(
            record["ok"], record["metrics"],
            cases[record["case_id"]].get("task_kind", "body")) else "fail"
        if row.get("machine_verdict") != machine or row.get("final_verdict") != machine:
            raise ValueError("Local audit reviewed verdict no longer matches scoring")
    if (review.get("reviewed_attempts") != expected_count
            or review.get("expected_attempts") != expected_count
            or review.get("total_attempts") != len(records)
            or review.get("unresolved") != 0
            or review.get("prior_75_audit_records_retained") is not True):
        raise ValueError("Local audit review summary is inconsistent")
    for relative, expected in review_contract.get("bound_receipts", {}).items():
        evidence_path = contained_path(source, relative)
        if (not evidence_path.is_file() or evidence_path.is_symlink()
                or _sha256(evidence_path) != expected):
            raise ValueError("Local audit review receipt changed: " + relative)
    return review


def _read_bound_receipt(source: Path, receipt: dict, relative: str) -> dict:
    expected = receipt.get("review", {}).get("bound_receipts", {}).get(relative)
    path = contained_path(source, relative)
    if not expected or not path.is_file() or path.is_symlink() or _sha256(path) != expected:
        raise ValueError("Local audit review receipt changed: " + relative)
    return json.loads(path.read_text(encoding="utf-8"))


def _require_lineage(source: Path, receipt: dict) -> None:
    """Check that the pinned receipts form one 75 + 25 -> 100 correction chain."""
    merge = _read_bound_receipt(source, receipt, "local-evidence/merge-receipt.json")
    supplement = _read_bound_receipt(
        source, receipt, "local-evidence/dynamic-25-full-result-audit.json")
    classification = _read_bound_receipt(
        source, receipt, "local-evidence/failure-classification-correction.json")
    prior_failures = _read_bound_receipt(
        source, receipt, "local-evidence/cohorts/75/native-failure-audit.json")
    qualification = _read_bound_receipt(
        source, receipt, "local-evidence/cohorts/75/qualification-review.json")
    correction = _read_bound_receipt(
        source, receipt, "local-evidence/local-audit-correction.json")
    bindings = receipt["bindings"]
    review = receipt["review"]
    review_correction_path = review.get("post_correction_receipt")
    if not isinstance(review_correction_path, str):
        raise ValueError("Local audit post-correction receipt is missing")
    review_correction = _read_bound_receipt(source, receipt, review_correction_path)
    if (merge.get("output", {}).get("dataset.json") != bindings.get("input_dataset_sha256")
            or merge.get("output", {}).get("results.jsonl") != bindings.get("input_results_sha256")
            or merge.get("output", {}).get("cases") != receipt.get("scope", {}).get("cases")
            or merge.get("output", {}).get("results") != receipt.get("scope", {}).get("results")
            or not all(merge.get("checks", {}).values())):
        raise ValueError("Local audit merge lineage is inconsistent")
    source_25 = merge.get("sources", {}).get("25", {})
    if (source_25.get("dataset_sha256") != review.get("supplement_dataset_sha256")
            or source_25.get("results_sha256") != review.get("supplement_results_sha256")
            or source_25.get("cases") != review.get("supplement_cases_reviewed")):
        raise ValueError("Local audit supplement lineage is inconsistent")
    supplement_bindings = supplement.get("bindings", {})
    if (supplement_bindings.get("dataset_sha256") != source_25.get("dataset_sha256")
            or supplement_bindings.get("results_sha256") != source_25.get("results_sha256")
            or len(supplement.get("cases", [])) != source_25.get("cases")
            or supplement.get("provider_calls_during_review") != 0
            or supplement.get("verdict_changes_after_current_scorer") != 0):
        raise ValueError("Local audit supplement audit is inconsistent")
    source_75 = merge.get("sources", {}).get("75", {})
    if (prior_failures.get("dataset_sha256") != source_75.get("dataset_sha256")
            or prior_failures.get("results_sha256") != source_75.get("results_sha256")
            or prior_failures.get("original_calls_added") != 0
            or qualification.get("summary", {}).get("supports_dynamic_qualification")
            != source_75.get("cases")):
        raise ValueError("Local audit retained 75-case lineage is inconsistent")
    classification_bindings = classification.get("bindings", {})
    if (classification_bindings.get("dataset_sha256") != bindings.get("input_dataset_sha256")
            or classification_bindings.get("results_after_sha256") != bindings.get("input_results_sha256")
            or classification.get("invariants", {}).get("verdicts_changed") is not False):
        raise ValueError("Local audit classification correction lineage is inconsistent")
    if (correction.get("output_dataset_sha256") != bindings.get("dataset_sha256")
            or correction.get("provider_calls") != 0
            or correction.get("task_verdict_changes") != []):
        raise ValueError("Local audit final correction lineage is inconsistent")
    if (review_correction.get("input_dataset_sha256")
            != correction.get("output_dataset_sha256")
            or review_correction.get("input_results_sha256")
            != correction.get("output_results_sha256")
            or review_correction.get("output_dataset_sha256")
            != bindings.get("dataset_sha256")
            or review_correction.get("output_results_sha256")
            != bindings.get("results_sha256")
            or review_correction.get("scoring_source_sha256")
            != bindings.get("scoring_source_sha256")
            or review_correction.get("provider_calls") != 0
            or review_correction.get("task_verdict_changes") != []):
        raise ValueError("PR review correction lineage is inconsistent")


def _require_local_batch_segments(batch):
    """A resumed batch must retain distinct physical manifests and the same policy."""
    segments = batch.get("segments")
    if segments is None:
        return
    if not isinstance(segments, list) or not segments:
        raise ValueError("Local measurement has no physical segments")
    attempts = []
    for segment in segments:
        for field in ("provider_config_sha256", "runtime_source_sha256", "parameters", "providers"):
            if segment.get(field) != batch.get(field):
                raise ValueError("Local measurement segment parameters differ")
        ids = segment.get("attempt_ids", [])
        if len(ids) != segment.get("attempt_count"):
            raise ValueError("Local measurement segment count differs")
        attempts.extend(ids)
    if (len(attempts) != len(set(attempts))
            or set(attempts) != set(batch.get("attempt_ids", []))
            or len(attempts) != batch.get("attempt_count")):
        raise ValueError("Local measurement segments repeat or omit a call")


def local_measurement_call_sha256(record):
    """Bind measured call facts independently of later scores/report metadata."""
    derived = {"metrics", "diagnosis", "failure_category", "scoring", "provider_metadata"}
    facts = {key: value for key, value in record.items() if key not in derived}
    facts["content_sha256"] = record.get("metrics", {}).get("content_sha256")
    return _canonical_sha256(facts)


def _require_local_scoring_revision(source, contract, records):
    from .scoring import SCORING_SOURCE_SHA256
    if any(r.get("metrics", {}).get("scoring_source_sha256") != SCORING_SOURCE_SHA256
           for r in records):
        raise ValueError("Local measurement has a stale scoring implementation")
    if contract.get("scoring_source_sha256") == SCORING_SOURCE_SHA256:
        return
    receipt = json.loads(contained_path(source, "local-evidence/scoring-correction.json").read_text())
    if (receipt.get("kind") != "offline_generic_parser_correction"
            or receipt.get("provider_calls") != 0
            or receipt.get("original_scoring_source_sha256") != contract.get("scoring_source_sha256")
            or receipt.get("corrected_scoring_source_sha256") != SCORING_SOURCE_SHA256
            or receipt.get("original_call_config_sha256") != contract.get("provider_config_sha256")
            or receipt.get("call_identities") != {
                r["attempt_id"]: local_measurement_call_sha256(r) for r in records}):
        raise ValueError("Invalid local scoring correction binding")
    from .scoring_regressions import build_correction_provenance
    provenance = receipt.get("provenance") or {}
    expected_provenance = build_correction_provenance(
        provenance.get("category"), provenance.get("root_cause"),
        provenance.get("regression_ids", []), receipt.get("task_verdict_changes", []))
    if provenance != expected_provenance:
        raise ValueError("Invalid local scoring correction provenance")
    rule_revision = receipt.get('rule_revision', False)
    if type(rule_revision) is not bool:
        raise ValueError('Local scoring rule revision must be explicit')
    for record in records:
        scoring = record.get("scoring", {})
        if (record.get("metrics", {}).get("scoring_source_sha256") != SCORING_SOURCE_SHA256
                or scoring.get("mode") != "offline_rescore"
                or scoring.get("input_results_sha256") != receipt.get("original_results_sha256")
                or scoring.get("rule_revision") is not rule_revision
                or scoring.get("reference_revision") is not False):
            raise ValueError("Local scoring correction changed rules or lost its predecessor")


def _require_local_record_correction(source, original, corrected, receipt=None):
    """Permit derived corrections while binding every physical call fact."""
    if receipt is None:
        path = contained_path(source, "local-evidence/scoring-correction.json")
        if not path.is_file():
            raise ValueError("Changed local record lacks a correction receipt")
        receipt = json.loads(path.read_text())
    identifier = original["attempt_id"]
    if (receipt.get("original_call_identities", {}).get(identifier)
            != local_measurement_call_sha256(original)
            or receipt.get("call_identities", {}).get(identifier)
            != local_measurement_call_sha256(corrected)):
        raise ValueError("Local correction lost its physical call binding")
    derived = {"metrics", "scoring", "diagnosis", "failure_category"}
    changed = {key for key in original.keys() | corrected.keys()
               if original.get(key) != corrected.get(key)} - derived
    if changed - {"final_url"}:
        raise ValueError("Local correction changed measured call facts")
    if "final_url" in changed:
        proof = receipt.get("field_projections", {}).get(identifier, {}).get("final_url", {})
        path = contained_path(source, proof.get("path", ""), reject_symlinks=True)
        if not path.is_file() or _sha256(path) != proof.get("sha256"):
            raise ValueError("Local URL correction lost its native response")
        value = json.loads(path.read_text())
        for key in proof.get("json_pointer", []):
            value = value.get(key) if isinstance(value, dict) else None
        if (not proof.get("json_pointer") or value != corrected.get("final_url")
                or proof.get("original_value") != original.get("final_url")):
            raise ValueError("Local URL correction differs from native response")


def score_reviewed_page_identity(source, case, record, annotation, captures, *, historical_scoring_source=None):
    """Apply a bound retrospective content correspondence, never a native URL."""
    from .metrics import normalize_for_match, task_measured
    from .scoring import score_attempt, _same_page_url, SCORING_SOURCE_SHA256

    if (annotation.get("attempt_id") != record["attempt_id"]
            or annotation.get("case_sha256") != _canonical_sha256(case)
            or annotation.get("call_sha256") != local_measurement_call_sha256(record)
            or annotation.get("content_sha256") != record["metrics"]["content_sha256"]
            or record.get("final_url") is not None
            or annotation.get("basis") != "retrospective_content_correspondence"
            or not annotation.get("reviewed_at")):
        raise ValueError("Page review lost its case, answer or physical call binding")
    historical = historical_scoring_source is not None and historical_scoring_source != SCORING_SOURCE_SHA256
    original_metrics = None if historical else score_attempt(
        case, contained_path(source, record["artifact_path"]).read_bytes(),
        ok=record["ok"], metadata=record.get("provider_metadata"),
        previous_metrics=record["metrics"], status_code=record.get("status_code"),
        title=record.get("title"), final_url=record.get("final_url"),
    )
    if not historical and (task_measured(original_metrics)
            or original_metrics.get("page_identity_status") != "unavailable"):
        raise ValueError("Page review may only fill an unavailable page identity")
    capture = captures[annotation["capture_index"]]
    corresponding_url = annotation["corresponding_url"]
    if (capture.get("url") != corresponding_url
            or _canonical_sha256(capture) != annotation.get("capture_sha256")):
        raise ValueError("Page review capture binding differs")
    answer = contained_path(source, record["artifact_path"]).read_bytes()
    if hashlib.sha256(answer).hexdigest() != annotation["content_sha256"]:
        raise ValueError("Page review answer changed")
    samples = annotation.get("matching_samples", [])
    saved = normalize_for_match(answer.decode("utf-8"))
    observed = normalize_for_match(capture["text"])
    normalized = [normalize_for_match(value).strip() for value in samples if isinstance(value, str)]
    strong_samples = {value for value in normalized if sum(char.isalnum() for char in value) >= 20}
    if (len(normalized) != len(samples) or len(strong_samples) < 2
            or any(not any(char.isalnum() for char in value)
                   or value not in saved or value not in observed for value in normalized)):
        raise ValueError("Page review needs matching substantive text in both bodies")
    navigation = (case.get("source_benchmark", {}).get("risk_control", {})
                  .get("navigation_contract", {}))
    acceptable = [navigation.get("requested_url"), navigation.get("observed_final_url")]
    if not any(value and _same_page_url(corresponding_url, value) for value in acceptable):
        raise ValueError("Reviewed page does not satisfy the frozen URL requirement")
    if historical:
        # The revision chain already hash-verifies these historical results.
        # Validate their review evidence without replaying a different scorer
        # and pretending its metrics were produced by the historical version.
        metrics = record['metrics']
        if (metrics.get('scoring_source_sha256') != historical_scoring_source
                or metrics.get('page_identity_required') is not True
                or metrics.get('page_identity_status') != 'verified'
                or metrics.get('final_url_consistent') is not True
                or metrics.get('content_chars') != len(answer.decode('utf-8'))
                or metrics.get('page_identity_review_sha256') != _canonical_sha256(annotation)):
            raise ValueError('Reviewed result differs from its bound historical shared scorer')
        return dict(metrics)
    metrics = score_attempt(
        case, answer, ok=record["ok"], metadata=record.get("provider_metadata"),
        previous_metrics=record["metrics"], status_code=record.get("status_code"),
        title=record.get("title"), final_url=corresponding_url,
    )
    metrics["page_identity_basis"] = "retrospective_content_correspondence"
    metrics["page_identity_review_sha256"] = _canonical_sha256(annotation)
    return metrics


def _require_page_identity_review(source, dataset, records, *, scoring_sources=None):
    binding = dataset.get("comparison", {}).get("page_identity_review")
    marked = {r["attempt_id"] for r in records
              if r["metrics"].get("page_identity_basis") == "retrospective_content_correspondence"}
    if binding is None:
        if marked:
            raise ValueError("Reviewed page identities lack a receipt")
        return
    path = contained_path(source, binding["path"], reject_symlinks=True)
    if _sha256(path) != binding["sha256"]:
        raise ValueError("Page identity review receipt changed")
    receipt = json.loads(path.read_text())
    capture_path = contained_path(source, receipt["captures_path"], reject_symlinks=True)
    if _sha256(capture_path) != receipt["captures_sha256"]:
        raise ValueError("Page review captures changed")
    captures = json.loads(capture_path.read_text())
    _require_identity_binding_revision(source, dataset, receipt)
    annotations = receipt["attempts"]
    if (receipt.get("kind") != "retrospective_page_identity_review"
            or receipt.get("provider_calls_added") != 0
            or len(annotations) != len(marked)
            or {a["attempt_id"] for a in annotations} != marked):
        raise ValueError("Page review scope differs from annotated results")
    cases = {case["id"]: case for case in dataset["cases"]}
    indexed = {r["attempt_id"]: r for r in records}
    for annotation in annotations:
        record = indexed[annotation["attempt_id"]]
        declared = None
        if scoring_sources is not None:
            declared = scoring_sources.get(record["attempt_id"])
            if not declared or record["metrics"].get("scoring_source_sha256") != declared:
                raise ValueError("Reviewed result lost its revision scoring source")
        expected = score_reviewed_page_identity(
            source, cases[record["case_id"]], record, annotation, captures,
            historical_scoring_source=declared)
        if expected != record["metrics"]:
            raise ValueError("Reviewed result differs from the shared scorer")


def _require_local_review(source, dataset, records):
    """Bind optional full independent review without certifying local evidence."""
    _require_page_identity_review(source, dataset, records)
    binding = dataset.get("comparison", {}).get("independent_review")
    if binding is None:
        return None
    path = contained_path(source, binding["path"], reject_symlinks=True)
    if _sha256(path) != binding.get("sha256"):
        raise ValueError("Local independent review binding changed")
    review = json.loads(path.read_text())
    rows = review.get("attempts", [])
    indexed = {row["attempt_id"]: row for row in rows}
    expected = {row["attempt_id"]: row for row in records}
    if (review.get("kind") != "local_independent_review"
            or review.get("review_protocol", {}).get("scope") != "all"
            or review.get("reviewed_attempts") != len(records)
            or review.get("expected_attempts") != len(records)
            or review.get("results_sha256") != _sha256(source / "results.jsonl")
            or len(indexed) != len(rows) or set(indexed) != set(expected)):
        raise ValueError("Local independent review scope differs")
    unresolved = 0
    from .metrics import task_measured
    for identifier, row in indexed.items():
        record = expected[identifier]
        verdict = ("unavailable" if not task_measured(record["metrics"]) else
                   "pass" if task_passed(record["ok"], record["metrics"]) else "fail")
        if (row.get("content_sha256") != record["metrics"]["content_sha256"]
                or row.get("current_verdict") != verdict
                or row.get("agent_verdict") not in {"pass", "fail", "insufficient"}
                or row.get("final_verdict") not in {verdict, None}):
            raise ValueError("Local review differs from scored evidence")
        if row.get("final_verdict") is None:
            unresolved += 1
            if row.get("status") != "deferred_unadjudicated":
                raise ValueError("Local unresolved review lacks explicit deferral")
    if review.get("unresolved") != unresolved:
        raise ValueError("Local review unresolved count differs")
    from .adjudication_evidence import require_adjudication_evidence
    require_adjudication_evidence(source, dataset, records, review)
    return review


def _require_local_method_bindings(contract, batches, field):
    """Join each method's execution to its declared runtime or configuration."""
    variants = contract.get(field + "_by_provider")
    expected = set(contract["providers"])
    label = "runtime" if field == "runtime_source_sha256" else "configuration"
    if variants is not None:
        if (not isinstance(variants, dict) or set(variants) != expected
                or any(not isinstance(value, str) or len(value) != 64
                       or any(char not in "0123456789abcdef" for char in value)
                       for value in variants.values())):
            raise ValueError(f"Invalid local {label} variant bindings")
    seen = set()
    for batch in batches:
        providers = batch.get("providers", [])
        if len(providers) != 1:
            raise ValueError(f"Local {label} batch must identify one method")
        name = providers[0].get("name")
        if name not in expected or name in seen:
            raise ValueError(f"Local {label} batches repeat or omit a method")
        seen.add(name)
        digest = variants[name] if variants is not None else contract[field]
        if batch.get(field) != digest:
            raise ValueError(f"Local measurement execution {label} differs")
    if seen != expected:
        raise ValueError(f"Local {label} batches repeat or omit a method")


def local_measurement_cases(contract: dict, source=None) -> list[dict]:
    """Read an ordered selection from the original hash-bound input trees."""
    bindings = contract.get("dataset_sources")
    if bindings is None:
        bindings = [{"path": contract["dataset_path"],
                     "sha256": contract["dataset_sha256"]}]
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("Local measurement dataset sources are missing")
    cases, seen_paths, seen_ids = [], set(), set()
    for binding in bindings:
        relative = binding["path"]
        root = source if contract.get("dataset_storage") == "evidence" else ROOT
        if root is None:
            raise ValueError("Evidence-relative inputs require their evidence root")
        path = contained_path(root, relative, reject_symlinks=True)
        raw = read_dataset_bytes(path)
        if relative in seen_paths or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError("Local measurement dataset source changed")
        seen_paths.add(relative)
        available = json.loads(raw)["cases"]
        selected = binding.get("case_ids", [case["id"] for case in available])
        if not selected or len(selected) != len(set(selected)):
            raise ValueError("Local measurement source selection differs")
        rows = [case for case in available if case["id"] in set(selected)]
        if [case["id"] for case in rows] != selected or seen_ids.intersection(selected):
            raise ValueError("Local measurement source selection differs")
        seen_ids.update(selected)
        cases.extend(rows)
    return cases


def _require_local_execution_segments(source, contract, batches, records):
    """Verify resumed measurements against retained pre-call plans and physical files."""
    correction_path = contained_path(source, "local-evidence/scoring-correction.json")
    correction = json.loads(correction_path.read_text()) if correction_path.is_file() else None
    def read(binding, *, json_lines=False):
        path = contained_path(source, binding["path"], reject_symlinks=True)
        if _sha256(path) != binding["sha256"]:
            raise ValueError("Local execution evidence binding changed")
        if json_lines:
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return json.loads(path.read_text())

    plans = contract["execution_plans"]
    if set(plans) not in ({"original", "resumed"}, {"original", "resumed", "admitted"}):
        raise ValueError("Local execution plans are incomplete")
    original, resumed = (read(plans[key]) for key in ("original", "resumed"))
    if (original.get("kind") != "uniform_native_local_measurement"
            or resumed.get("kind") != "post_merge_resume_and_runtime_correction"
            or original.get("providers") != contract["providers"]
            or original.get("dataset_sources") != contract.get("dataset_sources")
            or original.get("scoring_source_sha256") != contract["scoring_source_sha256"]
            or original.get("runs") != 1 or original.get("timeout_seconds") != 45
            or original.get("max_failure_retries") != 0):
        raise ValueError("Local execution changed the frozen protocol")
    expected_policy = {"runs": 1, "timeout": 45, "delay": original["delay"],
                       "provider_concurrency": 1, "max_failure_retries": 0}
    if resumed.get("parameters") != expected_policy:
        raise ValueError("Local resumed execution parameters differ")
    configs = {key: read(plans[key]["configuration"]) for key in plans}
    admitted = read(plans["admitted"]) if "admitted" in plans else None
    if admitted and (admitted.get("kind") != "bounded_admission_continuation"
                     or admitted.get("predecessor_plan_sha256") != plans["resumed"]["sha256"]
                     or admitted.get("parameters") != expected_policy
                     or admitted.get("providers") != contract["providers"]
                     or admitted.get("dataset_sha256") != resumed.get("dataset_sha256")
                     or configs["admitted"] != configs["resumed"]):
        raise ValueError("Local admission changed the frozen protocol or configuration")
    plan_values = {"original": original, "resumed": resumed}
    if admitted:
        plan_values["admitted"] = admitted
    for key, plan in plan_values.items():
        if plans[key]["configuration"]["sha256"] != plan["provider_config_sha256"]:
            raise ValueError("Local execution configuration differs from its pre-call plan")
    from copy import deepcopy
    normalized = deepcopy(configs["resumed"])
    for provider in normalized.get("providers", {}).values():
        for field in ("command", "required_paths"):
            if field in provider:
                provider[field] = [value.replace(
                    "{harness_root}/harness/web_fetch/workers/", "{harness_root}/scripts/oss/")
                    if isinstance(value, str) else value for value in provider[field]]
    if normalized != configs["original"]:
        raise ValueError("Local resumed configuration changed beyond worker paths")
    names = set(contract["providers"])
    scopes = resumed.get("case_ids_by_provider", {})
    cases = [case["id"] for case in local_measurement_cases(contract)]
    if set(scopes) != names or any(
        len(scope) != len(set(scope)) or not set(scope) <= set(cases)
        for scope in scopes.values()
    ):
        raise ValueError("Local resumed execution has an invalid task scope")
    admitted_scopes = admitted.get("case_ids_by_provider", {}) if admitted else {}
    if admitted and (set(admitted_scopes) != names or any(
            len(scope) != len(set(scope)) or not set(scope) <= set(scopes[name])
            for name, scope in admitted_scopes.items())):
        raise ValueError("Local admission has an invalid remaining task scope")
    by_attempt = {record["attempt_id"]: record for record in records}
    seen_names, seen_attempts = set(), set()
    for batch in batches:
        providers = batch.get("providers", [])
        if len(providers) != 1:
            raise ValueError("Local execution batch must identify one method")
        name = providers[0]["name"]
        if name not in names or name in seen_names:
            raise ValueError("Local execution repeats or omits a method")
        seen_names.add(name)
        segments = batch.get("segments", [])
        if not segments:
            raise ValueError("Local resumed execution has no physical segments")
        expected_scopes = {"resumed": scopes[name],
                           "original": [case for case in cases if case not in set(scopes[name])]}
        if admitted:
            expected_scopes["resumed"] = [case for case in scopes[name]
                                          if case not in set(admitted_scopes[name])]
            expected_scopes["admitted"] = admitted_scopes[name]
        declared_scopes = {"original": expected_scopes["original"],
                           "resumed": scopes[name]}
        if admitted:
            declared_scopes["admitted"] = admitted_scopes[name]
        seen_plans, ids, manifest_paths = set(), [], set()
        offsets = {key: 0 for key in plans}
        for segment in segments:
            key = segment.get("plan_id")
            if key not in plans or (key == "original" and key in seen_plans):
                raise ValueError("Local execution repeats or substitutes a physical segment")
            seen_plans.add(key)
            plan = plan_values[key]
            manifest_path = segment["manifest"]["path"]
            if manifest_path in manifest_paths:
                raise ValueError("Local execution repeats a physical manifest")
            manifest_paths.add(manifest_path)
            manifest = read(segment["manifest"])
            physical = read(segment["records"], json_lines=True)
            full_ids = [f"{name}__{case}__r001" for case in expected_scopes[key]]
            expected_ids = full_ids[offsets[key]:offsets[key] + len(physical)]
            if not physical:
                raise ValueError("Local execution has an empty physical segment")
            offsets[key] += len(physical)
            if ([row.get("attempt_id") for row in physical] != expected_ids
                    or segment.get("attempt_ids") != expected_ids
                    or segment.get("attempt_count") != len(expected_ids)):
                raise ValueError("Local execution segment task selection differs")
            if (manifest.get("runtime_source_sha256") != plan["runtime_source_sha256"]
                    or manifest.get("provider_config_sha256") != plan["provider_config_sha256"]
                    or manifest.get("providers") != segment.get("providers")
                    or manifest.get("parameters") != segment.get("parameters")):
                raise ValueError("Local execution segment differs from its physical manifest")
            if (len(manifest["providers"]) != 1 or manifest["providers"][0]["name"] != name
                    or any(manifest["parameters"].get(field) != value
                           for field, value in expected_policy.items())):
                raise ValueError("Local execution segment parameters differ")
            if key != "original" and (
                not manifest.get("completed_at")
                or manifest.get("attempt_ids") != expected_ids
                or manifest.get("attempt_count") != len(expected_ids)
            ):
                raise ValueError("Local resumed execution is incomplete")
            if key != "original" and manifest.get("unexecuted"):
                if (physical[-1].get("error_type") != "timeout"
                        or any(item.get("reason") !=
                               "non_cancellable_call_still_running; remaining provider cases not executed"
                               or item.get("after_attempt_id") != physical[-1]["attempt_id"]
                               for item in manifest["unexecuted"])):
                    raise ValueError("Local execution stopped for an unaccounted reason")
            if key == "admitted" and manifest.get("scheduling") != admitted.get("scheduling"):
                raise ValueError("Local admission manifest changed the scheduling contract")
            continuation = segment.get("continuation_plan")
            if continuation:
                continuation = read(continuation)
                expected_kind = {"resumed": "resume_unexecuted_after_process_exit",
                                 "admitted": "resume_unexecuted_with_admission"}.get(key)
                parent_field = "admission_plan_sha256" if key == "admitted" else "resumed_plan_sha256"
                if (key == "original" or continuation.get("kind") != expected_kind
                        or continuation.get("method") != name
                        or continuation.get(parent_field) != plans[key]["sha256"]
                        or continuation.get("parameters") != expected_policy
                        or continuation.get("provider_config_sha256") != plan["provider_config_sha256"]
                        or continuation.get("runtime_source_sha256") != plan["runtime_source_sha256"]
                        or continuation.get("dataset_sha256") != plan.get("dataset_sha256")
                        or continuation.get("case_ids") != declared_scopes[key][offsets[key] - len(physical):]):
                    raise ValueError("Local continuation differs from its pre-call scope")
            for row in physical:
                identifier = row["attempt_id"]
                if identifier in seen_attempts or identifier not in by_attempt:
                    raise ValueError("Local execution altered or repeated a measured result")
                if row != by_attempt[identifier]:
                    _require_local_record_correction(source, row, by_attempt[identifier], correction)
                seen_attempts.add(identifier)
            ids.extend(expected_ids)
        if (any(offsets[key] != len(scope) for key, scope in expected_scopes.items())
                or seen_plans != {key for key, scope in expected_scopes.items() if scope}
                or len(ids) != batch.get("attempt_count")
                or set(ids) != set(batch.get("attempt_ids", []))):
            raise ValueError("Local execution segments repeat or omit a call")
    if seen_names != names or seen_attempts != set(by_attempt):
        raise ValueError("Local execution lineage is incomplete")


def require_complete_local_inventory(dataset: dict, records: list[dict]) -> dict:
    """Allow explicit unknown verdicts locally, never missing physical results."""
    from .reliability import assess_reliability

    comparison = dataset.get("comparison", {})
    if (comparison.get("kind") != "uniform_native_local_measurement"
            or comparison.get("publication_state") != "local_audit_only_not_for_release"):
        raise ValueError("Incomplete adjudication is only allowed in a local measurement")
    reliability = assess_reliability(dataset, records)
    if (not reliability["expected_calls"] or reliability["missing"]
            or reliability["extra"] or reliability["duplicates"]):
        raise ValueError("Local measurement physical inventory is incomplete")
    return reliability


def require_local_execution_contract(source, dataset, records, contract, execution):
    """Shared physical-call boundary for complete cohorts and task replacements."""
    from .artifacts import verify_record_public_call_logs, record_public_call_log_bindings
    from datetime import datetime
    if (contract.get("schema_version") != 1
            or contract.get("kind") != "uniform_native_local_measurement"
            or contract.get("runs") != 1 or contract.get("timeout_seconds") != 45
            or contract.get("max_failure_retries") != 0):
        raise ValueError("Invalid uniform native local measurement contract")
    canonical = {"cases": (local_measurement_cases(contract, source)
                              if contract.get("dataset_storage") == "evidence"
                              else local_measurement_cases(contract))}
    if dataset["cases"] != canonical["cases"]:
        raise ValueError("Local measurement changed the committed cases")
    expected = {(p, c["id"]) for p in contract["providers"] for c in canonical["cases"]}
    if (len(records) != len(expected)
            or {(r["provider"], r["case_id"]) for r in records} != expected
            or any(r["run"] != 1 or r.get("provider_metadata", {}).get("retry") for r in records)):
        raise ValueError("Local measurement matrix is incomplete or has retries")
    if len(execution["batches"]) != len(contract["providers"]):
        raise ValueError("Local measurement execution scope differs")
    if contract.get("execution_plans"):
        _require_local_execution_segments(source, contract, execution["batches"], records)
    else:
        for field in ("runtime_source_sha256", "provider_config_sha256"):
            _require_local_method_bindings(contract, execution["batches"], field)
    for batch in execution["batches"]:
        if not contract.get("execution_plans"):
            _require_local_batch_segments(batch)
        ids = batch.get("attempt_ids", [])
        measured_ids = {r["attempt_id"] for r in records
                        if r["provider"] == batch["providers"][0]["name"]}
        if (len(ids) != len(set(ids)) or set(ids) != measured_ids
                or batch.get("attempt_count") != len(measured_ids)):
            raise ValueError("Local measurement execution calls differ from results")
        if (batch.get("parameters", {}).get("timeout") != 45
                or batch.get("parameters", {}).get("runs") != 1
                or batch.get("parameters", {}).get("provider_concurrency") != 1
                or batch.get("parameters", {}).get("max_failure_retries") != 0):
            raise ValueError("Local measurement execution parameters differ")
    if contract.get("runner_inputs"):
        _require_local_runner_inputs(source, contract, execution, records)
    canonical_by_id = {case['id']: case for case in canonical['cases']}
    for record in records:
        if record.get('url') != canonical_by_id[record['case_id']]['url']:
            raise ValueError('Local measurement request URL differs from frozen task')
        if len(record_public_call_log_bindings(record)) != 1:
            raise ValueError("Local measurement requires one complete call log per result")
        lexical = source
        for part in Path(record["artifact_path"]).parts:
            lexical = lexical / part
            if lexical.is_symlink():
                raise ValueError("Local measurement output is unsafe")
        artifact = contained_path(source, record["artifact_path"])
        if artifact.is_symlink() or _sha256(artifact) != record["metrics"]["content_sha256"]:
            raise ValueError("Local measurement output changed")
        verify_record_public_call_logs(source, record)
        binding = record_public_call_log_bindings(record)[0]
        log = json.loads(contained_path(source, binding["path"]).read_text())
        if (any(log["identity"][key] != record[key]
                for key in ("attempt_id", "provider", "case_id", "run"))
                or log["output"]["path"] != record["artifact_path"]
                or log["output"]["sha256"] != record["metrics"]["content_sha256"]
                or log['request']['url'] != record['url']
                or log['timing'].get('elapsed_ms') != record.get('elapsed_ms')
                or not (datetime.fromisoformat(record['started_at'])
                        <= datetime.fromisoformat(log['timing']['started_at'])
                        <= datetime.fromisoformat(log['timing']['completed_at'])
                        <= datetime.fromisoformat(record['completed_at']))
                or log['timing'].get('deadline_budget_ms') != 45000):
            raise ValueError("Local measurement call log belongs to a different result")


def require_local_measurement(source: Path, dataset: dict, records: list[dict]) -> None:
    """Validate a new complete local matrix without claiming independent review."""
    contract = json.loads(contained_path(source, "local-run-contract.json").read_text())
    execution = json.loads(contained_path(source, "execution.json").read_text())
    if dataset.get("comparison", {}).get("local_run_contract_sha256") != _sha256(source / "local-run-contract.json"):
        raise ValueError("Local measurement pre-call contract changed")
    _require_local_scoring_revision(source, contract, records)
    require_local_execution_contract(source, dataset, records, contract, execution)
    require_complete_local_inventory(dataset, records)
    return _require_local_review(source, dataset, records)


def require_local_audit(source: Path, dataset: dict, records: list[dict]) -> dict:
    """Validate final inputs, complete evidence inventory, and inherited review."""
    comparison = dataset.get("comparison", {})
    if comparison.get("kind") == "uniform_native_local_measurement":
        if comparison.get("publication_state") != "local_audit_only_not_for_release":
            raise ValueError("Local measurement cannot claim certification")
        return require_local_measurement(source, dataset, records)
    relative = comparison.get("local_audit_receipt")
    if comparison.get("publication_state") != "local_audit_only_not_for_release":
        raise ValueError("Local audit mode requires a non-publishable dataset")
    if not isinstance(relative, str):
        raise ValueError("Local audit dataset does not declare its receipt")
    receipt_path = contained_path(ROOT, relative)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "local_audit_complete":
        raise ValueError("Local audit receipt is not complete")
    bindings = receipt.get("bindings", {})
    for name, key in (("dataset.json", "dataset_sha256"),
                      ("results.jsonl", "results_sha256")):
        if _sha256(contained_path(source, name)) != bindings.get(key):
            raise ValueError("Local audit receipt binding mismatch: " + name)
    _require_repository_bindings(dataset, receipt)
    manifest = _require_manifest(source, receipt)
    for name in ("dataset.json", "results.jsonl", "result-review.json"):
        if manifest.get("bindings", {}).get(name) != _sha256(contained_path(source, name)):
            raise ValueError("Local audit manifest binding mismatch: " + name)
    _require_lineage(source, receipt)
    return _require_review(source, dataset, records, receipt, manifest)


def _require_local_runner_inputs(source, contract, execution, records):
    """Verify retained runner inputs and source observations, without certifying them."""
    from datetime import datetime
    from .local_revision import read_bound
    inputs = contract['runner_inputs']
    def read(key):
        return json.loads(read_bound(source, inputs[key]).read_text())
    frozen, configuration, network = (read(key) for key in ('dataset', 'providers', 'network'))
    canonical = local_measurement_cases(contract, source)
    if frozen['cases'] != canonical or inputs['dataset'] != contract['dataset_sources'][0]:
        raise ValueError('Local runner dataset differs from frozen task inputs')
    if inputs['providers']['sha256'] != contract['provider_config_sha256']:
        raise ValueError('Local runner configuration binding differs')
    if not set(contract['providers']) <= set(configuration['providers']):
        raise ValueError('Local runner configuration omits a method')
    if network.get('dns_mode') != 'system' or network.get('usable_for_guarded_local_comparison') is not True:
        raise ValueError('Local runner lacks usable system DNS evidence')
    from .network_preflight import GUARDED_NATIVE_TYPES, require_usable_public_dns
    require_usable_public_dns(network)
    from urllib.parse import urlsplit
    method_types = {name: configuration['providers'][name].get('method_type',
                    configuration['providers'][name]['type']) for name in contract['providers']}
    if any(value in GUARDED_NATIVE_TYPES for value in method_types.values()):
        hosts = network.get('hosts', [])
        expected_hosts = {urlsplit(case['url']).hostname for case in canonical}
        if (len(hosts) != len(expected_hosts) or {host.get('host') for host in hosts} != expected_hosts
                or any(not host.get('addresses') or host.get('dns_error') for host in hosts)):
            raise ValueError('Local runner system DNS scope is incomplete')
    for batch in execution['batches']:
        name = batch['providers'][0]['name']
        guarded = method_types[name] in GUARDED_NATIVE_TYPES
        if (batch['providers'][0].get('type') != method_types[name]
                or (guarded and not batch['providers'][0].get('implementation_identity'))
                or any(row.get('provider_type') != method_types[name]
                       for row in records if row['provider'] == name)):
            raise ValueError('Local runner method identity differs from frozen configuration')
        expected_network = ({'dns_mode': 'system', 'sha256': inputs['network']['sha256']}
                            if guarded else None)
        if (batch.get('dataset_sha256') != inputs['dataset']['sha256']
                or batch.get('environment') != contract['environment']
                or batch.get('parameters') != contract['parameters']
                or batch.get('network_preflight') != expected_network
                or not batch.get('completed_at') or batch.get('unexecuted')):
            raise ValueError('Local runner execution differs from bound inputs or environment')
    if any(not batch.get('environment', {}).get(key) for batch in execution['batches']
           for key in ('python', 'platform')):
        raise ValueError('Local runner execution lacks environment identity')
    observations = inputs['source_observations']
    if set(observations) != {case['id'] for case in canonical}:
        raise ValueError('Local runner source observation scope differs')
    for case in canonical:
        times = [datetime.fromisoformat(row['started_at']) for row in records if row['case_id'] == case['id']]
        ends = [datetime.fromisoformat(row['completed_at']) for row in records if row['case_id'] == case['id']]
        for stage in ('pre', 'post'):
            capture = observations[case['id']][stage]
            evidence = json.loads(read_bound(source, capture['evidence']).read_text())
            for key, field in [('source', 'sha256'),
                               ('reference', 'reference_sha256'), ('extracted', 'extracted_sha256')]:
                read_bound(source, capture[key])
                if capture[key]['sha256'] != evidence.get(field):
                    raise ValueError('Local runner source bytes differ from observation')
            observed = datetime.fromisoformat(evidence['checked_at'])
            if (evidence.get('case_id') != case['id'] or evidence.get('url') != case['url']
                    or evidence.get('independent') is not True or evidence.get('status') != 200
                    or (stage == 'pre' and observed > min(times))
                    or (stage == 'post' and observed < max(ends))):
                raise ValueError('Local runner source observation does not bracket the calls')


def _hint_migrated_case(case):
    import copy
    result = copy.deepcopy(case)
    hints = result.pop('forbidden_any', [])
    if hints:
        result['diagnostic_any'] = list(dict.fromkeys(result.get('diagnostic_any', []) + hints))
    result.pop('acceptance_policy_version', None)
    return result


def _require_identity_binding_revision(source, dataset, receipt):
    from .local_revision import read_bound
    migration = receipt.get('binding_revision')
    if not migration:
        return
    previous = json.loads(read_bound(source, migration['prior_receipt']).read_text())
    old_cases = json.loads(read_bound(source, migration['prior_cases']).read_text())
    cases = {case['id']: case for case in dataset['cases']}
    if (migration.get('kind') != 'diagnostic_field_migration'
            or migration.get('new_independent_review') is not False
            or receipt.get('captures_path') != previous.get('captures_path')
            or receipt.get('captures_sha256') != previous.get('captures_sha256')):
        raise ValueError('Page identity evidence migration changed source observations')
    annotations = []
    for annotation in previous['attempts']:
        identifier = annotation['attempt_id'].split('__')[1]
        old = old_cases[identifier]
        migrated = _hint_migrated_case(old)
        # Older compiled navigation tasks implied identity acceptance. Keep
        # that requirement explicit when adopting the diagnostic default.
        if (cases[identifier].get('page_identity_required') is True
                and 'page_identity_required' not in old):
            navigation = old.get('source_benchmark', {}).get('risk_control', {}).get('navigation_contract', {})
            dynamic_identity = (old.get('track') == 'dynamic' and old.get('evaluation_url')
                                and old.get('expected_final_url') and old.get('target_language')
                                and old.get('target_region'))
            if ((old.get('track') == 'risk_control' and navigation.get('requested_url')
                 and navigation.get('observed_final_url')) or dynamic_identity):
                migrated['page_identity_required'] = True
        if (annotation['case_sha256'] != _canonical_sha256(old)
                or migrated != cases[identifier]):
            raise ValueError('Page identity migration changed its target contract')
        annotations.append({**annotation, 'prior_case_sha256': annotation['case_sha256'],
                            'case_sha256': _canonical_sha256(cases[identifier])})
    if receipt['attempts'] != annotations:
        raise ValueError('Page identity migration changed an existing reviewed annotation')


def carry_reviewed_page_identities(source, before_dataset, dataset, records):
    """Reapply retained human evidence after offline scoring; never invent a URL."""
    from .local_revision import read_bound
    binding = before_dataset.get('comparison', {}).get('page_identity_review')
    if not binding:
        return None
    original = json.loads(read_bound(source, binding).read_text())
    captures_path = contained_path(source, original['captures_path'], reject_symlinks=True)
    if _sha256(captures_path) != original['captures_sha256']:
        raise ValueError('Page identity capture changed before rescoring')
    captures = json.loads(captures_path.read_text())
    old_cases = {case['id']: case for case in before_dataset['cases']}
    cases = {case['id']: case for case in dataset['cases']}
    indexed = {row['attempt_id']: row for row in records}
    changed = any(annotation['case_sha256'] != _canonical_sha256(cases[indexed[annotation['attempt_id']]['case_id']])
                  for annotation in original['attempts'])
    receipt = original
    if changed:
        prior_cases = {indexed[a['attempt_id']]['case_id']: old_cases[indexed[a['attempt_id']]['case_id']]
                       for a in original['attempts']}
        key = _canonical_sha256({'prior_receipt': binding, 'cases': {key: cases[key] for key in prior_cases}})[:16]
        directory = source / 'local-evidence/page-identity-review/rebindings' / key
        directory.mkdir(parents=True, exist_ok=True)
        old_path = directory / 'prior-cases.json'
        prior_bytes = (json.dumps(prior_cases, ensure_ascii=False, indent=2)+'\n').encode()
        if old_path.exists() and old_path.read_bytes() != prior_bytes:
            raise ValueError('Page identity migration would overwrite prior case evidence')
        old_path.write_bytes(prior_bytes)
        receipt = {key: original[key] for key in ('kind', 'reviewed_at', 'basis', 'provider_calls_added', 'captures_path', 'captures_sha256')}
        receipt['attempts'] = [{**a, 'prior_case_sha256': a['case_sha256'],
            'case_sha256': _canonical_sha256(cases[indexed[a['attempt_id']]['case_id']])} for a in original['attempts']]
        receipt['binding_revision'] = {'kind': 'diagnostic_field_migration', 'prior_receipt': binding,
            'prior_cases': {'path': str(old_path.relative_to(source)), 'sha256': _sha256(old_path)},
            'new_independent_review': False}
        _require_identity_binding_revision(source, dataset, receipt)
        path = directory / 'receipt.json'
        raw = (json.dumps(receipt, ensure_ascii=False, indent=2)+'\n').encode()
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Page identity migration would overwrite a prior receipt')
        path.write_bytes(raw)
        binding = {'path': str(path.relative_to(source)), 'sha256': _sha256(path)}
    dataset['comparison']['page_identity_review'] = binding
    for annotation in receipt['attempts']:
        row = indexed[annotation['attempt_id']]
        row['metrics'] = score_reviewed_page_identity(source, cases[row['case_id']], row, annotation, captures)
    _require_page_identity_review(source, dataset, records)
    return binding
