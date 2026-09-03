"""Offline, bounded reference checks for new body measurements.

No reference is refreshed here. Independent observations must be supplied by the
caller. A completed certificate describes one run, not freshness for another.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .metrics import normalize_for_match
from .render_evidence import validate_render_reference
from .safety import contained_path, safe_identifier

MAX_REFERENCE_AGE_SECONDS = 30 * 60


class ReferenceLifecycleError(ValueError):
    """Reference uncertainty is never a provider failure."""

    def __init__(self, message: str, status: str = "reference_unverified"):
        super().__init__(message)
        self.status = status


def _get(case, key, default=None):
    return case.get(key, default) if isinstance(case, dict) else getattr(case, key, default)


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def timestamp(value) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    raise ReferenceLifecycleError("Invalid reference timestamp: explicit timezone required")


def _now(now=None) -> datetime:
    return timestamp(now) if now is not None else datetime.now(timezone.utc)


def _fresh(value, now: datetime) -> datetime:
    checked = timestamp(value)
    if not 0 <= (now - checked).total_seconds() <= MAX_REFERENCE_AGE_SECONDS:
        raise ReferenceLifecycleError("Reference capture is future-dated or older than 30 minutes")
    return checked


def requires_fresh_reference(case) -> bool:
    # Tiny synthetic fixtures intentionally have no independent verification.
    # Production body cases carry this metadata, including invalid attestations.
    return (_get(case, "track", "web") in {"web", "dynamic", "challenge"}
            and _get(case, "task_kind", "body") == "body"
            and bool((_get(case, "ground_truth_main_content", "") or "").strip())
            and bool(_get(case, "source_verification", {})))


def scope_identity(case) -> dict[str, Any]:
    verification = _get(case, "source_verification", {}) or {}
    benchmark = _get(case, "source_benchmark", {}) or {}
    render = benchmark.get("render_evidence", {})
    return deepcopy({
        "case_id": _get(case, "id"), "url": _get(case, "url"),
        "track": _get(case, "track", "web"), "task_kind": _get(case, "task_kind", "body"),
        "task_prompt": _get(case, "task_prompt", ""),
        "reference_scope": verification.get("reference_scope"),
        "selector": verification.get("selector"),
        "source_benchmark": {key: value for key, value in benchmark.items() if key != "render_evidence"},
        "render_scope": render.get("scope"),
        "evaluation_contract": {key: _get(case, key, default) for key, default in (
            ("expected_any", []), ("required_facts", []), ("required_patterns", []),
            ("forbidden_any", []), ("min_content_chars", 1), ("fact_coverage_threshold", 1.0),
            ("content_score_applicable", True), ("expected_resources", []), ("reference_answer", ""))},
    })


def _completion(completion) -> None:
    if not isinstance(completion, dict) or completion.get("satisfied") is not True:
        raise ReferenceLifecycleError("Independent completion was not satisfied")
    elapsed, timeout = completion.get("elapsed_ms"), completion.get("timeout_ms")
    if (type(elapsed) not in (int, float) or type(timeout) not in (int, float)
            or not math.isfinite(elapsed) or not math.isfinite(timeout)
            or not 0 <= elapsed <= timeout or timeout <= 0):
        raise ReferenceLifecycleError("Independent completion is unbounded or exceeded its deadline")


def _binding(case, dataset_path: Path, now: datetime) -> dict[str, Any]:
    identifier = safe_identifier(_get(case, "id"))
    verification = _get(case, "source_verification")
    reference = _get(case, "ground_truth_main_content")
    if not isinstance(verification, dict) or verification.get("state") != "valid":
        raise ReferenceLifecycleError(f"{identifier}: independent source verification is not valid")
    if not isinstance(verification.get("method"), str) or not verification["method"].strip():
        raise ReferenceLifecycleError(f"{identifier}: independent verification method is missing")
    _fresh(verification.get("checked_at"), now)
    actual_hash = sha256(reference)
    if verification.get("reference_sha256") != actual_hash:
        raise ReferenceLifecycleError(f"{identifier}: reference SHA-256 mismatch", "reference_changed")
    binding = {
        "reference_sha256": actual_hash,
        "expected_normalized_sha256": sha256(normalize_for_match(reference)),
        "checked_at": verification["checked_at"],
        "scope_identity": scope_identity(case),
        "source_verification": deepcopy(verification),
    }
    if _get(case, "track", "web") == "dynamic":
        render = (_get(case, "source_benchmark", {}) or {}).get("render_evidence", {})
        completion = render.get("completion", {})
        _completion(completion)
        _fresh(completion.get("checked_at"), now)
        source = Path("sources") / identifier
        root = Path(dataset_path).parent
        try:
            initial = contained_path(root, source / "initial.html.txt").read_bytes()
            rendered_raw = contained_path(root, source / "rendered.txt").read_bytes()
            reference_raw = contained_path(root, source / "reference.txt").read_bytes()
            evidence_raw = contained_path(root, source / "evidence.json").read_bytes()
            evidence = json.loads(evidence_raw)
            rendered = rendered_raw.decode("utf-8")
        except (OSError, ValueError) as error:
            raise ReferenceLifecycleError(f"{identifier}: missing or invalid paired source evidence") from error
        if not isinstance(evidence, dict):
            raise ReferenceLifecycleError(f"{identifier}: invalid source evidence")
        _fresh(evidence.get("checked_at"), now)
        _fresh(evidence.get("initial_checked_at"), now)
        if (evidence.get("case_id") != identifier or evidence.get("url") != _get(case, "url")
                or evidence.get("completion") != render.get("scope")):
            raise ReferenceLifecycleError(f"{identifier}: paired source scope mismatch")
        audit = validate_render_reference(case, initial, rendered)
        if not audit["valid"]:
            raise ReferenceLifecycleError(f"{identifier}: incomplete render reference: {audit['errors']}")
        if (sha256(reference_raw) != actual_hash
                or normalize_for_match(rendered) != normalize_for_match(reference)
                or any(evidence.get(key) != value for key, value in (
                    ("initial_sha256", sha256(initial)), ("rendered_sha256", sha256(rendered_raw)),
                    ("reference_sha256", actual_hash)))):
            raise ReferenceLifecycleError(f"{identifier}: paired source hash/content mismatch", "reference_changed")
        count = (_get(case, "source_benchmark", {}) or {}).get("expected_entries")
        if count is not None and (evidence.get("expected_entries") != count or evidence.get("observed_entries") != count):
            raise ReferenceLifecycleError(f"{identifier}: incomplete declared source scope")
        binding["render_evidence"] = deepcopy(render)
        binding["source_files_sha256"] = {str(source / name): sha256(raw) for name, raw in (
            ("initial.html.txt", initial), ("rendered.txt", rendered_raw),
            ("reference.txt", reference_raw), ("evidence.json", evidence_raw))}
    return binding


def preflight_reference_checks(cases, dataset_path: Path, now=None) -> dict | None:
    """Read-only preflight; call before creating outputs or invoking providers."""
    checked = _now(now)
    bindings = {}
    for case in cases:
        if requires_fresh_reference(case):
            identifier = _get(case, "id")
            if identifier in bindings:
                raise ReferenceLifecycleError("Duplicate reference case")
            bindings[identifier] = _binding(case, dataset_path, checked)
    if not bindings:
        return None
    return {"schema_version": 1, "status": "awaiting_postcheck",
            "preflight_checked_at": checked.isoformat(),
            "max_age_seconds": MAX_REFERENCE_AGE_SECONDS, "cases": bindings}


def check_case_reference_fresh(case, dataset_path: Path, binding: dict | None, now=None) -> None:
    """Recheck after throttling, immediately before fetch, outside its exception handler."""
    if binding is None and not requires_fresh_reference(case):
        return
    if binding is None or not requires_fresh_reference(case):
        raise ReferenceLifecycleError("Reference eligibility changed during the run", "reference_changed")
    if _binding(case, dataset_path, _now(now)) != binding:
        raise ReferenceLifecycleError("Frozen reference binding changed during the run", "reference_changed")


def check_case_reference_binding(case, binding: dict) -> None:
    """Offline rescores must use the original capture; no freshness claim is made."""
    reference = _get(case, "ground_truth_main_content", "")
    if (sha256(reference) != binding.get("reference_sha256")
            or sha256(normalize_for_match(reference)) != binding.get("expected_normalized_sha256")
            or scope_identity(case) != binding.get("scope_identity")
            or _get(case, "source_verification", {}) != binding.get("source_verification")
            or ("render_evidence" in binding and
                (_get(case, "source_benchmark", {}) or {}).get("render_evidence") != binding["render_evidence"])):
        raise ReferenceLifecycleError("Rescore requires the original frozen reference and capture binding", "reference_changed")


def _selected_records(batch, records):
    ids = batch.get("attempt_ids")
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or batch.get("attempt_count") != len(ids)):
        raise ReferenceLifecycleError("Incomplete batch attempt identity")
    selected = [r for r in records if r.get("attempt_id") in ids]
    if len(selected) != len(ids) or {r.get("attempt_id") for r in selected} != set(ids):
        raise ReferenceLifecycleError("Missing or duplicate measured attempts")
    start, end = timestamp(batch.get("started_at")), timestamp(batch.get("completed_at"))
    if end < start:
        raise ReferenceLifecycleError("Invalid batch time bounds")
    for record in selected:
        began = timestamp(record.get("started_at"))
        elapsed = record.get("elapsed_ms")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ReferenceLifecycleError("Invalid call elapsed time")
        finished = began + timedelta(milliseconds=elapsed)
        # New runners retain the exact finish; older records have start + elapsed.
        if record.get("completed_at") is not None:
            finished = max(finished, timestamp(record["completed_at"]))
        if not start <= began <= finished <= end + timedelta(milliseconds=.01):
            raise ReferenceLifecycleError("Call outside batch time bounds")
    return selected


def _postcheck_case(binding, case, observation, records, now):
    check_case_reference_binding(case, binding)
    if observation.get("independent") is not True:
        raise ReferenceLifecycleError("Observation must attest independent capture, not provider output")
    if observation.get("scope_identity") != binding["scope_identity"]:
        raise ReferenceLifecycleError("Observation scope identity mismatch")
    _completion(observation.get("completion"))
    checked = _fresh(observation.get("checked_at"), now)
    if not records:
        raise ReferenceLifecycleError("Reference case has no measured call")
    finishes = []
    for record in records:
        if record.get("metrics", {}).get("reference_sha256") != binding["reference_sha256"]:
            raise ReferenceLifecycleError("Measured reference hash mismatch")
        finish = timestamp(record["started_at"]) + timedelta(milliseconds=record["elapsed_ms"])
        if record.get("completed_at") is not None:
            finish = max(finish, timestamp(record["completed_at"]))
        finishes.append(finish)
    latest = max(finishes)
    if not 0 < (checked - latest).total_seconds() <= MAX_REFERENCE_AGE_SECONDS:
        raise ReferenceLifecycleError("Observation must follow the latest relevant call within 30 minutes")
    content = observation.get("content_text")
    if not isinstance(content, str) or not content.strip():
        raise ReferenceLifecycleError("Observation content is missing")
    actual = sha256(content)
    normalized = sha256(normalize_for_match(content))
    if "content_sha256" in observation and observation["content_sha256"] != actual:
        raise ReferenceLifecycleError("Observation content hash mismatch")
    return {"status": "verified" if normalized == binding["expected_normalized_sha256"] else "reference_changed",
            "checked_at": observation["checked_at"], "latest_call_finished_at": latest.isoformat(),
            "content_sha256": actual, "normalized_sha256": normalized}


def finalize_reference_checks(run_dir: Path, observations_path: Path, now=None) -> dict:
    """Finalize manifest/execution batches using an offline observation list.

    JSON is a list (or {"observations": [...]}) of objects containing case_id,
    independent: true, content_text, checked_at, scope_identity (copied exactly
    from the binding), and completion {satisfied, elapsed_ms, timeout_ms}.
    Only reference_checks.status/postcheck may change in original manifests.
    Legacy batches are left untouched and never acquire a freshness claim.
    """
    root = Path(run_dir).resolve()
    checked = _now(now)
    documents = {}
    for filename in ("manifest.json", "execution.json"):
        path = contained_path(root, filename)
        if path.exists():
            documents[filename] = json.loads(path.read_bytes())
    batches = []
    if "manifest.json" in documents:
        batches.append(documents["manifest.json"])
    if "execution.json" in documents:
        execution_batches = documents["execution.json"].get("batches")
        if not isinstance(execution_batches, list) or not execution_batches:
            raise ReferenceLifecycleError("Invalid execution batches")
        batches.extend(execution_batches)
    guarded = [b for b in batches if "reference_checks" in b]
    if not guarded:
        raise ReferenceLifecycleError("No live reference lifecycle; historical batches cannot be certified retroactively")
    raw = Path(observations_path).read_bytes()
    # Hash-addressed, exclusive evidence files never replace existing raw data.
    evidence_rel = f"reference-checks/observations-{sha256(raw)}.json"
    evidence_path = contained_path(root, evidence_rel)
    if evidence_path.exists() and evidence_path.read_bytes() != raw:
        raise ReferenceLifecycleError("Postcheck evidence hash pointer mismatch")
    records_raw = contained_path(root, "results.jsonl").read_bytes()
    records = [json.loads(line) for line in records_raw.splitlines() if line.strip()]
    dataset_raw = contained_path(root, "dataset.json").read_bytes()
    dataset_cases = json.loads(dataset_raw)["cases"]
    cases = {_get(c, "id"): c for c in dataset_cases}
    if len(cases) != len(dataset_cases):
        raise ReferenceLifecycleError("Duplicate dataset case IDs")
    expected = {key for b in guarded for key in b["reference_checks"]["cases"]}
    observations, input_error = {}, None
    try:
        payload = json.loads(raw)
        rows = payload.get("observations") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ReferenceLifecycleError("Missing observation list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
                raise ReferenceLifecycleError("Invalid observation")
            identifier = row["case_id"]
            if identifier in observations or identifier not in expected:
                raise ReferenceLifecycleError("Duplicate or unknown observation case")
            observations[identifier] = row
        if observations.keys() != expected:
            raise ReferenceLifecycleError("Missing required case observations")
    except (ValueError, TypeError) as error:
        input_error = str(error)
    outcomes = []
    for batch in guarded:
        checks = batch["reference_checks"]
        results = {}
        try:
            if input_error:
                raise ReferenceLifecycleError(input_error)
            selected = _selected_records(batch, records)
            for identifier, binding in checks["cases"].items():
                try:
                    if identifier not in cases:
                        raise ReferenceLifecycleError("Reference case absent from frozen dataset")
                    results[identifier] = _postcheck_case(binding, cases[identifier], observations[identifier],
                        [r for r in selected if r["case_id"] == identifier], checked)
                except (ReferenceLifecycleError, KeyError, TypeError, ValueError, OverflowError) as error:
                    results[identifier] = {"status": getattr(error, "status", "reference_unverified"), "error": str(error)}
        except (ReferenceLifecycleError, KeyError, TypeError, ValueError, OverflowError) as error:
            results = {identifier: {"status": "reference_unverified", "error": str(error)} for identifier in checks["cases"]}
        statuses = {row["status"] for row in results.values()}
        status = ("reference_changed" if "reference_changed" in statuses else
                  "verified" if statuses == {"verified"} else "reference_unverified")
        # Never erase an observed change, expired pre-call check, or earlier
        # failed postcheck by retrying until the page happens to match again.
        if checks.get("status") in {"reference_changed", "reference_unverified"}:
            status = checks["status"]
        if "postcheck" in checks:
            raise ReferenceLifecycleError("Batch already postchecked; retained certificate is immutable")
        checks["status"] = status
        checks["postcheck"] = {"checked_at": checked.isoformat(), "status": status,
            "evidence_path": evidence_rel, "evidence_sha256": sha256(raw),
            "results_sha256": sha256(records_raw), "dataset_sha256": sha256(dataset_raw), "cases": results}
        outcomes.append(status)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    if not evidence_path.exists():
        with evidence_path.open("xb") as stream:
            stream.write(raw)
    for filename, payload in documents.items():
        # Do not rewrite a legacy-only manifest alongside lifecycle batches.
        affected = ([payload] if filename == "manifest.json" else payload["batches"])
        if any("reference_checks" in batch for batch in affected):
            contained_path(root, filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "reference_changed" if "reference_changed" in outcomes else
            "verified" if set(outcomes) == {"verified"} else "reference_unverified",
            "batches": outcomes, "evidence_path": evidence_rel, "evidence_sha256": sha256(raw)}


def require_verified_reference_checks(manifests, *, cases=None, records=None, source=None) -> None:
    """Fail closed for explicit live lifecycles; leave historical batches readable.

    Call before report/append writes. The minimum call validates certificate
    structure, per-case success, capture/finish time bounds and evidence pointer.
    Optional cases (sequence or ID mapping) and records verify portable bindings
    without comparing whole merged dataset/results byte hashes. Optional source
    verifies retained observation bytes against their hash and certificate.
    This checks a completed historical certificate, NOT freshness as of today.
    """
    if isinstance(manifests, dict):
        manifests = [manifests]
    if not isinstance(manifests, (list, tuple)):
        raise ReferenceLifecycleError("Expected manifest batches")
    case_map = None
    if cases is not None:
        if isinstance(cases, dict) and "cases" in cases and isinstance(cases["cases"], list):
            cases = cases["cases"]
        values = list(cases.values()) if isinstance(cases, dict) else list(cases)
        case_map = {_get(case, "id"): case for case in values}
        if len(case_map) != len(values):
            raise ReferenceLifecycleError("Duplicate reference case IDs")

    def valid_hash(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    try:
        for batch in manifests:
            if not isinstance(batch, dict):
                raise ReferenceLifecycleError("Invalid manifest")
            if "reference_checks" not in batch:
                continue
            checks = batch["reference_checks"]
            if not isinstance(checks, dict) or checks.get("status") != "verified":
                raise ReferenceLifecycleError("Live reference checks are not verified")
            bindings, post = checks.get("cases"), checks.get("postcheck")
            if (checks.get("schema_version") != 1
                    or checks.get("max_age_seconds") != MAX_REFERENCE_AGE_SECONDS
                    or not isinstance(bindings, dict) or not bindings
                    or not isinstance(post, dict) or post.get("status") != "verified"
                    or not isinstance(post.get("cases"), dict) or post["cases"].keys() != bindings.keys()):
                raise ReferenceLifecycleError("Missing or incomplete per-case reference certificate")
            for key in ("evidence_sha256", "dataset_sha256", "results_sha256"):
                if not valid_hash(post.get(key)):
                    raise ReferenceLifecycleError("Invalid reference certificate hash")
            if post.get("evidence_path") != f"reference-checks/observations-{post['evidence_sha256']}.json":
                raise ReferenceLifecycleError("Invalid reference certificate evidence pointer")
            preflight_at = timestamp(checks.get("preflight_checked_at"))
            finalized_at = timestamp(post.get("checked_at"))
            if finalized_at < preflight_at:
                raise ReferenceLifecycleError("Reference postcheck precedes preflight")
            selected = _selected_records(batch, records) if records is not None else None
            observations = None
            if source is not None:
                raw = contained_path(Path(source), post["evidence_path"]).read_bytes()
                if sha256(raw) != post["evidence_sha256"]:
                    raise ReferenceLifecycleError("Reference certificate evidence hash mismatch")
                payload = json.loads(raw)
                rows = payload.get("observations") if isinstance(payload, dict) else payload
                if not isinstance(rows, list):
                    raise ReferenceLifecycleError("Invalid certificate observations")
                observations = {}
                for row in rows:
                    if (not isinstance(row, dict) or not isinstance(row.get("case_id"), str)
                            or row["case_id"] in observations):
                        raise ReferenceLifecycleError("Invalid or duplicate certificate observation")
                    observations[row["case_id"]] = row
                if not bindings.keys() <= observations.keys():
                    raise ReferenceLifecycleError("Missing certificate observations")
            for identifier, binding in bindings.items():
                safe_identifier(identifier)
                result = post["cases"][identifier]
                if (not isinstance(result, dict) or result.get("status") != "verified"
                        or not valid_hash(binding.get("reference_sha256"))
                        or not valid_hash(binding.get("expected_normalized_sha256"))
                        or not valid_hash(result.get("content_sha256"))
                        or result.get("normalized_sha256") != binding["expected_normalized_sha256"]
                        or not isinstance(binding.get("scope_identity"), dict)
                        or binding["scope_identity"].get("case_id") != identifier
                        or not isinstance(binding["scope_identity"].get("evaluation_contract"), dict)):
                    raise ReferenceLifecycleError("Invalid or nonmatching case postcheck")
                _fresh(binding.get("checked_at"), preflight_at)
                observed_at = _fresh(result.get("checked_at"), finalized_at)
                finished_at = timestamp(result.get("latest_call_finished_at"))
                if not preflight_at <= finished_at < observed_at:
                    raise ReferenceLifecycleError("Invalid postcheck call/capture ordering")
                _fresh(finished_at, observed_at)
                if case_map is not None:
                    if identifier not in case_map:
                        raise ReferenceLifecycleError("Certificate case absent from dataset")
                    check_case_reference_binding(case_map[identifier], binding)
                relevant = [r for r in selected if r["case_id"] == identifier] if selected is not None else None
                if relevant is not None:
                    if not relevant:
                        raise ReferenceLifecycleError("Certificate has no measured reference call")
                    latest = None
                    for record in relevant:
                        if record.get("metrics", {}).get("reference_sha256") != binding["reference_sha256"]:
                            raise ReferenceLifecycleError("Record reference SHA differs from certificate")
                        began = timestamp(record["started_at"])
                        _fresh(binding["checked_at"], began)
                        finish = began + timedelta(milliseconds=record["elapsed_ms"])
                        if record.get("completed_at") is not None:
                            finish = max(finish, timestamp(record["completed_at"]))
                        latest = finish if latest is None else max(latest, finish)
                    if latest != finished_at:
                        raise ReferenceLifecycleError("Certificate does not follow the latest relevant call")
                if observations is not None:
                    observation = observations[identifier]
                    _completion(observation.get("completion"))
                    content = observation.get("content_text")
                    if (observation.get("independent") is not True or not isinstance(content, str) or not content.strip()
                            or observation.get("scope_identity") != binding["scope_identity"]
                            or observation.get("checked_at") != result["checked_at"]
                            or sha256(content) != result["content_sha256"]
                            or sha256(normalize_for_match(content)) != result["normalized_sha256"]
                            or ("content_sha256" in observation and observation["content_sha256"] != sha256(content))):
                        raise ReferenceLifecycleError("Postcheck evidence differs from certificate")
    except ReferenceLifecycleError:
        raise
    except (OSError, KeyError, AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ReferenceLifecycleError(f"Invalid reference certificate: {error}") from error
