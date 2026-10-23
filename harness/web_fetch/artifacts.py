"""Hash-bound private provider artifacts.

Raw API envelopes are useful audit evidence, but may contain account-specific or
sensitive fields.  They stay in the persistent evidence tree and are verified at
every aggregation/publication boundary; they are never projected into a report.
"""
from __future__ import annotations

import hashlib
from harness.record_io import file_sha256
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.web_fetch.safety import reject_evidence_symlinks

PRIVATE_ARTIFACT_ROOT = "private_artifacts"
PUBLIC_CALL_LOG_ROOT = "call-logs"


def file_scoring_metadata(case, metadata: dict, source: Path) -> dict:
    """Recheck delivered file bytes against current rules without changing call evidence."""
    from lexbench_webfetch.file_acceptance import evaluate_files

    case = case if isinstance(case, dict) else vars(case)
    evaluation = metadata.get("file_evaluation")
    if case.get("task_kind") != "file":
        return metadata
    # No delivered-file claim is a normal failed delivery. A malformed saved
    # claim is corrupted evidence, not a new provider failure or a stale score.
    if "file_evaluation" not in metadata:
        return metadata
    if not isinstance(evaluation, dict):
        raise ValueError("Delivered file evaluation must be an object")
    delivered = evaluation.get("files", [])
    if not isinstance(delivered, list):
        raise ValueError("Delivered files must be a list")
    for item in delivered:
        if (not isinstance(item, dict) or not isinstance(item.get("artifact_path"), str)
                or not item["artifact_path"] or not isinstance(item.get("sha256"), str)):
            raise ValueError("Delivered file requires an artifact_path and sha256 binding")
    payloads = []
    for item in delivered:
        raw = contained_path(source, item["artifact_path"], reject_symlinks=True).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
            raise ValueError("Delivered file integrity failure: " + item["artifact_path"])
        payloads.append(raw)
    current = evaluate_files(case, payloads)
    if len(current["files"]) != len(delivered):
        raise ValueError("Delivered file evaluation changed the file count")
    current["files"] = [{**old, **new} for old, new in zip(delivered, current["files"], strict=True)]
    return {**metadata, "file_evaluation": current}


def public_call_log_binding(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a public call-log binding and reject partial/unsafe claims."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Public call-log binding must be an object")
    if set(value) != {"path", "sha256", "bytes"}:
        raise ValueError("Public call-log binding must contain only path, sha256 and bytes")
    path = Path(str(value.get("path", "")))
    if not path.parts or path.parts[0] != PUBLIC_CALL_LOG_ROOT:
        raise ValueError("Public call logs must stay under call-logs")
    digest, size = value.get("sha256"), value.get("bytes")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("Public call log has an invalid SHA-256 binding")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("Public call log has an invalid byte-size binding")
    return {"path": path.as_posix(), "sha256": digest, "bytes": size}


def record_public_call_log_bindings(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect the final and every retained retry call log."""
    values: list[dict[str, Any]] = []
    metadata = record.get("provider_metadata", {})
    for attempt in metadata.get("retry", {}).get("attempts", []):
        binding = public_call_log_binding(attempt.get("call_log"))
        if binding:
            values.append(binding)
    final = public_call_log_binding(metadata.get("call_log"))
    if final:
        values.append(final)
    seen: dict[str, tuple[str, int]] = {}
    for binding in values:
        identity = binding["sha256"], binding["bytes"]
        if binding["path"] in seen and seen[binding["path"]] != identity:
            raise ValueError("Public call log has conflicting bindings: " + binding["path"])
        seen[binding["path"]] = identity
    return [dict(path=path, sha256=digest, bytes=size)
            for path, (digest, size) in seen.items()]


def verify_public_call_log(root: Path, binding: dict[str, Any], *, allow_public_views=False) -> Path:
    """Verify one public call log's containment, bytes, digest and closed schema."""
    normalized = public_call_log_binding(binding)
    if normalized is None:  # pragma: no cover - caller supplied an object
        raise ValueError("Public call-log binding is empty")
    lexical = root.resolve()
    for part in Path(normalized["path"]).parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise ValueError("Public call log is missing or unsafe: " + normalized["path"])
    path = contained_path(root, normalized["path"])
    if not path.is_file() or path.stat().st_size != normalized["bytes"]:
        raise ValueError("Public call log is missing or has changed: " + normalized["path"])
    binary = path.read_bytes()
    if hashlib.sha256(binary).hexdigest() != normalized["sha256"]:
        raise ValueError("Public call log hash changed: " + normalized["path"])
    try:
        payload = json.loads(binary)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Public call log is not valid JSON: " + normalized["path"]) from error
    expected = {"schema_version", "identity", "timing", "request", "response",
                "process", "output", "error", "events", "capture"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("Public call log has an unexpected top-level schema")
    schemas = {
        "identity": {"call_id", "attempt_id", "provider", "provider_type", "case_id",
                     "run", "physical_attempt"},
        "timing": {"started_at", "completed_at", "elapsed_ms", "retry_throttle_wait_ms",
                   "deadline_budget_ms", "deadline_overrun"},
        "request": {"method", "route", "url", "summary"},
        "response": {"status_code", "upstream_status_code", "final_url", "content_type",
                     "request_id"},
        "process": {"exit_code", "stdout", "stdout_bytes", "stdout_truncated",
                    "stderr", "stderr_bytes", "stderr_truncated"},
        "output": {"path", "bytes", "sha256", "empty"},
        "error": {"type", "message", "exception_class", "stack"},
    }
    if payload.get("schema_version") != 1:
        raise ValueError("Public call log has an unsupported schema version")
    for key, fields in schemas.items():
        if not isinstance(payload.get(key), dict) or set(payload[key]) != fields:
            raise ValueError(f"Public call log has an unexpected {key} schema")
    legacy_capture = {"record", "response_metadata", "process_metadata", "process_stdout",
                      "process_stderr", "request_summary", "events", "raw_response", "output"}
    current_capture = legacy_capture | {"diagnostic_log"}
    if (not isinstance(payload.get("capture"), dict)
            or frozenset(payload["capture"]) not in {frozenset(legacy_capture),
                                                      frozenset(current_capture)}):
        raise ValueError("Public call log has an unexpected capture schema")
    stack = payload["error"]["stack"]
    if not isinstance(stack, list) or any(
        not isinstance(frame, dict) or set(frame) != {"file", "line", "function"}
        for frame in stack
    ):
        raise ValueError("Public call log has an unexpected error stack schema")
    events = payload["events"]
    if (not isinstance(events, list) or len(events) < 3
            or any(not isinstance(event, dict)
                   or set(event) != {"stage", "at", "status_code", "error_type"}
                   for event in events)
            or events[0]["stage"] != "call_started"
            or events[-1]["stage"] != "call_completed"
            or not any(event["stage"] in {"response_received", "exception_raised", "answer_deadline_exceeded"}
                       for event in events[1:-1])):
        raise ValueError("Public call log has an unexpected event schema")
    if any(event["stage"] == "answer_deadline_exceeded" and event["error_type"] != "timeout"
           for event in events):
        raise ValueError("Deadline event must identify a timeout")
    summary = payload["request"]["summary"]
    if summary is not None and not isinstance(summary, dict):
        raise ValueError("Public call log has an unexpected request summary")
    output_path = Path(str(payload["output"]["path"]))
    if not output_path.parts or output_path.parts[0] != "artifacts":
        raise ValueError("Public call log output must stay under artifacts")
    claimed = payload["output"]
    if allow_public_views:
        from .publication_privacy import read_evidence_binding, original_evidence_size
        answer = {'path': output_path.as_posix(), 'sha256': claimed['sha256']}
        read_evidence_binding(root, answer)
        size = original_evidence_size(root, answer)
        if claimed['bytes'] != size or claimed['empty'] is not (size == 0):
            raise ValueError("Public call log original output binding changed")
    else:
        output = contained_path(root, output_path)
        if not output.is_file() or output.is_symlink():
            raise ValueError("Public call log output is missing or unsafe")
        output_bytes = output.read_bytes()
        if (claimed["bytes"] != len(output_bytes)
                or claimed["sha256"] != hashlib.sha256(output_bytes).hexdigest()
                or claimed["empty"] is not (len(output_bytes) == 0)):
            raise ValueError("Public call log output binding changed")
    return path


def verify_record_public_call_logs(root: Path, record: dict[str, Any], *, allow_public_views=False) -> None:
    for binding in record_public_call_log_bindings(record):
        verify_public_call_log(root, binding, allow_public_views=allow_public_views)


def raw_response_binding(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Return a complete raw-response binding, rejecting partial claims."""
    values = {
        "path": metadata.get("raw_response_path"),
        "sha256": metadata.get("raw_response_sha256"),
        "bytes": metadata.get("raw_response_bytes"),
    }
    present = {key for key, value in values.items() if value is not None}
    if not present:
        return None
    if present != set(values):
        raise ValueError("Raw response evidence binding is incomplete")
    path = Path(str(values["path"]))
    if not path.parts or path.parts[0] != PRIVATE_ARTIFACT_ROOT:
        raise ValueError("Raw response evidence must stay under private_artifacts")
    digest = values["sha256"]
    size = values["bytes"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("Raw response evidence has an invalid SHA-256 binding")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("Raw response evidence has an invalid byte-size binding")
    return values


def diagnostic_log_binding(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Return a complete raw diagnostic-log binding, rejecting partial claims."""
    values = {
        "path": metadata.get("diagnostic_log_path"),
        "sha256": metadata.get("diagnostic_log_sha256"),
        "bytes": metadata.get("diagnostic_log_bytes"),
    }
    present = {key for key, value in values.items() if value is not None}
    if not present:
        return None
    if present != set(values):
        raise ValueError("Diagnostic log evidence binding is incomplete")
    path = Path(str(values["path"]))
    if not path.parts or path.parts[0] != PRIVATE_ARTIFACT_ROOT:
        raise ValueError("Diagnostic log evidence must stay under private_artifacts")
    digest = values["sha256"]
    size = values["bytes"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("Diagnostic log evidence has an invalid SHA-256 binding")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("Diagnostic log evidence has an invalid byte-size binding")
    return values


def record_private_bindings(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect final and retained-retry private artifacts from one result."""
    bindings: list[dict[str, Any]] = []
    final = raw_response_binding(record.get("provider_metadata", {}))
    if final:
        bindings.append(final)
    diagnostic = diagnostic_log_binding(record.get("provider_metadata", {}))
    if diagnostic:
        bindings.append(diagnostic)
    for attempt in record.get("provider_metadata", {}).get("retry", {}).get("attempts", []):
        private = attempt.get("private_artifacts", [])
        if not isinstance(private, list):
            raise ValueError("Retry private_artifacts must be a list")
        for binding in private:
            if not isinstance(binding, dict):
                raise ValueError("Retry private artifact binding must be an object")
            normalized = raw_response_binding({
                "raw_response_path": binding.get("path"),
                "raw_response_sha256": binding.get("sha256"),
                "raw_response_bytes": binding.get("bytes"),
            })
            if normalized:
                bindings.append(normalized)
    seen: dict[str, tuple[str, int]] = {}
    for binding in bindings:
        identity = (binding["sha256"], binding["bytes"])
        if binding["path"] in seen and seen[binding["path"]] != identity:
            raise ValueError("Private artifact has conflicting bindings: " + binding["path"])
        seen[binding["path"]] = identity
    return [dict(path=path, sha256=digest, bytes=size)
            for path, (digest, size) in seen.items()]


def verify_private_binding(root: Path, binding: dict[str, Any]) -> Path:
    """Verify a private artifact without returning or decoding its contents."""
    normalized = raw_response_binding({
        "raw_response_path": binding.get("path"),
        "raw_response_sha256": binding.get("sha256"),
        "raw_response_bytes": binding.get("bytes"),
    })
    if normalized is None:  # pragma: no cover - all-empty is invalid for a caller
        raise ValueError("Private artifact binding is empty")
    # ``contained_path`` resolves links to enforce containment. Check the
    # lexical chain as well so an in-tree link cannot masquerade as evidence.
    lexical = root.resolve()
    for part in Path(normalized["path"]).parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise ValueError("Private artifact is missing or unsafe: " + normalized["path"])
    path = contained_path(root, normalized["path"])
    if not path.is_file():
        raise ValueError("Private artifact is missing or unsafe: " + normalized["path"])
    stat = path.stat()
    if stat.st_size != normalized["bytes"]:
        raise ValueError("Private artifact byte size changed: " + normalized["path"])
    if file_sha256(path) != normalized["sha256"]:
        raise ValueError("Private artifact hash changed: " + normalized["path"])
    return path


def verify_record_private_artifacts(root: Path, record: dict[str, Any]) -> None:
    metadata = record.get("provider_metadata", {})
    if "_raw_response_b64" in metadata:
        raise ValueError("Encoded raw response leaked into persisted result metadata")
    if "_diagnostic_log_b64" in metadata:
        raise ValueError("Encoded diagnostic log leaked into persisted result metadata")
    for binding in record_private_bindings(record):
        verify_private_binding(root, binding)


def materialize_private_artifacts(
    destination: Path, source: Path, records: Iterable[dict[str, Any]]
) -> None:
    """Copy only verified private bindings between persistent evidence trees."""
    for record in records:
        for binding in record_private_bindings(record):
            origin = verify_private_binding(source, binding)
            target = contained_path(destination, binding["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                verify_private_binding(destination, binding)
            else:
                shutil.copy2(origin, target)
                verify_private_binding(destination, binding)


def artifact_bindings(record: dict[str, Any]) -> list[tuple[str, str]]:
    """Enumerate declared public evidence, including every retained physical call."""
    bindings = {}

    def add(relative, digest):
        if relative in bindings and bindings[relative] != digest:
            raise ValueError('Artifact has conflicting hash bindings: ' + relative)
        bindings[relative] = digest

    add(record['artifact_path'], record['metrics']['content_sha256'])
    metadata = record.get('provider_metadata', {})
    for path_key, hash_key in (('rendered_html_path', 'rendered_html_sha256'),
                               ('raw_stdout_path', 'raw_stdout_sha256')):
        if metadata.get(path_key):
            add(metadata[path_key], metadata[hash_key])
    for item in metadata.get('file_evaluation', {}).get('files', []):
        if item.get('artifact_path'):
            add(item['artifact_path'], item['sha256'])
    for attempt in metadata.get('retry', {}).get('attempts', []):
        hashes = attempt.get('artifact_sha256', {})
        primary_digest = attempt.get('content_sha256') or hashes.get(attempt['artifact_path'])
        if not primary_digest:
            raise ValueError('Retry artifact has no hash binding: ' + attempt['artifact_path'])
        add(attempt['artifact_path'], primary_digest)
        for relative in attempt.get('artifact_paths', []):
            digest = hashes.get(relative)
            if relative == attempt['artifact_path']:
                digest = digest or primary_digest
            if not digest:
                raise ValueError('Retry artifact has no hash binding: ' + relative)
            add(relative, digest)
    for binding in record_public_call_log_bindings(record):
        add(binding['path'], binding['sha256'])
    return list(bindings.items())


def verify_record_artifacts(root: Path, record: dict[str, Any], *, native_cases=None) -> None:
    """Verify all declared public and private bytes against acquisition bindings."""
    verify_record_private_artifacts(root, record)
    for relative, digest in artifact_bindings(record):
        if (not isinstance(relative, str) or not isinstance(digest, str)
                or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)):
            raise ValueError('Incomplete artifact binding: ' + str(record.get('attempt_id')))
        source = contained_path(root, relative, reject_symlinks=True)
        if not source.is_file() or file_sha256(source) != digest:
            raise ValueError('Artifact differs from its result binding: ' + relative)
    verify_record_public_call_logs(root, record)
    from harness.web_fetch.native_routes import verify_saved_native_answer
    verify_saved_native_answer(root, record, cases_by_id=native_cases)


def materialize_bound_artifacts(output: Path, source_root: Path, records: list[dict]) -> None:
    """Verify acquisition-time authority before copying declared evidence."""
    copies = {}
    from .native_routes import native_cases_by_id
    native_cases = native_cases_by_id(source_root)
    for record in records:
        verify_record_artifacts(source_root, record, native_cases=native_cases)
        for relative, digest in artifact_bindings(record):
            source = contained_path(source_root, relative, reject_symlinks=True)
            target = contained_path(output, relative, reject_symlinks=True)
            if target.exists() and file_sha256(target) != digest:
                raise ValueError('Aggregate artifact collision: ' + relative)
            if relative in copies and copies[relative][2] != digest:
                raise ValueError('Aggregate artifact collision: ' + relative)
            copies[relative] = (source, target, digest)
    materialize_private_artifacts(output, source_root, records)
    for source, target, digest in copies.values():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        if file_sha256(target) != digest:
            raise ValueError('Copied artifact changed: ' + str(target.relative_to(output)))
    for record in records:
        verify_record_public_call_logs(output, record)


def materialize_reference_checks(output: Path, source: Path, batches: list[dict]) -> None:
    """Retain hash-bound source observations and native execution contracts."""
    from .native_routes import materialize_native_route_contracts
    materialize_native_route_contracts(output, source, batches)
    for batch in batches:
        post = batch.get('reference_checks', {}).get('postcheck', {})
        if not post:
            continue
        relative, digest = post.get('evidence_path'), post.get('evidence_sha256')
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in '0123456789abcdef' for char in digest)
                or relative != f'reference-checks/observations-{digest}.json'):
            raise ValueError('Reference check has an invalid evidence binding')
        path = contained_path(source, relative, reject_symlinks=True)
        if not path.is_file() or file_sha256(path) != digest:
            raise ValueError('Reference check evidence differs from its certificate')
        target = contained_path(output, relative, reject_symlinks=True)
        if target.exists():
            if not target.is_file() or file_sha256(target) != digest:
                raise ValueError('Conflicting reference check evidence')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        if file_sha256(target) != digest:
            raise ValueError('Copied reference check evidence changed')


def materialize_task_references(output: Path, trees: dict[str, Path], case_ids: Iterable[str]) -> None:
    """Copy selected task references from already validated input trees."""
    identifiers = list(dict.fromkeys(safe_identifier(value) for value in case_ids))
    for name in ('sources', 'list-references'):
        root = trees[name]
        reject_evidence_symlinks(root)
        if root.exists() and not root.is_dir():
            raise ValueError('Reference input tree must be a directory: ' + name)
        destination = output / name
        destination.mkdir(parents=True, exist_ok=True)
        files = []
        for identifier in identifiers:
            relative = identifier if name == 'sources' else identifier + '.json'
            path = contained_path(root, relative, reject_symlinks=True)
            if not path.exists():
                continue
            if name == 'sources':
                if not path.is_dir():
                    raise ValueError('Task source must be a directory: ' + identifier)
                files.extend(child for child in path.rglob('*') if child.is_file())
            else:
                if not path.is_file():
                    raise ValueError('Task list reference must be a file: ' + identifier)
                files.append(path)
        for path in files:
            target = contained_path(destination, path.relative_to(root.resolve()),
                                    reject_symlinks=True)
            digest = file_sha256(path)
            if target.exists():
                if not target.is_file() or file_sha256(target) != digest:
                    raise ValueError('Conflicting task reference: ' + str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            if file_sha256(target) != digest:
                raise ValueError('Copied task reference changed: ' + str(target))


def copy_cohort_evidence(source, target, dataset, records, batches, *,
                         summary_name, include_private=False, include_metadata=True):
    """Copy declared comparison evidence; publication excludes private envelopes."""
    if summary_name not in {'speed-comparison.json', 'resource-comparison.json'}:
        raise ValueError('Unsupported comparison summary')
    source = Path(source)
    reject_evidence_symlinks(source)

    allowed = set()
    if include_metadata:
        allowed.update({'dataset.json', 'results.jsonl', summary_name})
        for optional in ('execution.json', 'manifest.json', 'scoring-revisions.json',
                         'reference-revisions.json', 'latency-corrections.json'):
            if (source / optional).is_file():
                allowed.add(optional)
        if include_private and (source / 'result-review.json').is_file():
            allowed.add('result-review.json')
        if 'latency-corrections.json' in allowed:
            from .reference_lifecycle import latency_correction_original_elapsed
            latency_correction_original_elapsed(source)
    native_cases = {case['id']: case for case in dataset['cases']}
    for record in records:
        verify_record_artifacts(source, record, native_cases=native_cases)
        allowed.update(relative for relative, _ in artifact_bindings(record))
        if include_private:
            allowed.update(binding['path'] for binding in record_private_bindings(record))

    target.mkdir(parents=True)
    materialize_reference_checks(target, source, batches)
    materialize_task_references(target, {name: source / name
        for name in ('sources', 'list-references')}, [case['id'] for case in dataset.get('cases', [])])
    for relative in sorted(allowed):
        source_file = contained_path(source, relative, reject_symlinks=True)
        if not source_file.is_file():
            raise ValueError('Comparison cohort allowlisted file is missing: ' + relative)
        target_file = contained_path(target, relative, reject_symlinks=True)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)

    for record in records:
        for relative, digest in artifact_bindings(record):
            if file_sha256(contained_path(target, relative, reject_symlinks=True)) != digest:
                raise ValueError('Copied comparison artifact changed: ' + relative)
        verify_record_public_call_logs(target, record)
