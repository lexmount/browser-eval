"""Fail-closed validation for explicitly non-publishable local audit bundles."""
import hashlib
import json
from pathlib import Path

from .metrics import task_passed
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
    source_root = ROOT / "evaluation/sources"
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
                "path": path.relative_to(ROOT).as_posix(),
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


def require_local_audit(source: Path, dataset: dict, records: list[dict]) -> dict:
    """Validate final inputs, complete evidence inventory, and inherited review."""
    comparison = dataset.get("comparison", {})
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
