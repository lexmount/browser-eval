"""Hash-bound private provider artifacts.

Raw API envelopes are useful audit evidence, but may contain account-specific or
sensitive fields.  They stay in the persistent evidence tree and are verified at
every aggregation/publication boundary; they are never projected into a report.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from .safety import contained_path

PRIVATE_ARTIFACT_ROOT = "private_artifacts"
PUBLIC_CALL_LOG_ROOT = "call-logs"


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


def verify_public_call_log(root: Path, binding: dict[str, Any]) -> Path:
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
            or not any(event["stage"] in {"response_received", "exception_raised"}
                       for event in events[1:-1])):
        raise ValueError("Public call log has an unexpected event schema")
    summary = payload["request"]["summary"]
    if summary is not None and not isinstance(summary, dict):
        raise ValueError("Public call log has an unexpected request summary")
    output_path = Path(str(payload["output"]["path"]))
    if not output_path.parts or output_path.parts[0] != "artifacts":
        raise ValueError("Public call log output must stay under artifacts")
    output = contained_path(root, output_path)
    if not output.is_file() or output.is_symlink():
        raise ValueError("Public call log output is missing or unsafe")
    output_bytes = output.read_bytes()
    claimed = payload["output"]
    if (claimed["bytes"] != len(output_bytes)
            or claimed["sha256"] != hashlib.sha256(output_bytes).hexdigest()
            or claimed["empty"] is not (len(output_bytes) == 0)):
        raise ValueError("Public call log output binding changed")
    return path


def verify_record_public_call_logs(root: Path, record: dict[str, Any]) -> None:
    for binding in record_public_call_log_bindings(record):
        verify_public_call_log(root, binding)


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
    if hashlib.sha256(path.read_bytes()).hexdigest() != normalized["sha256"]:
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


def verify_record_artifacts(root: Path, record: dict[str, Any]) -> None:
    """Verify all declared public and private bytes against acquisition bindings."""
    verify_record_private_artifacts(root, record)
    for relative, digest in artifact_bindings(record):
        if (not isinstance(relative, str) or not isinstance(digest, str)
                or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)):
            raise ValueError('Incomplete artifact binding: ' + str(record.get('attempt_id')))
        source = contained_path(root, relative, reject_symlinks=True)
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('Artifact differs from its result binding: ' + relative)
    verify_record_public_call_logs(root, record)


def materialize_bound_artifacts(output: Path, source_root: Path, records: list[dict]) -> None:
    """Verify acquisition-time authority before copying declared evidence."""
    copies = {}
    for record in records:
        verify_record_artifacts(source_root, record)
        for relative, digest in artifact_bindings(record):
            source = contained_path(source_root, relative, reject_symlinks=True)
            target = contained_path(output, relative, reject_symlinks=True)
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Aggregate artifact collision: ' + relative)
            if relative in copies and copies[relative][2] != digest:
                raise ValueError('Aggregate artifact collision: ' + relative)
            copies[relative] = (source, target, digest)
    materialize_private_artifacts(output, source_root, records)
    for source, target, digest in copies.values():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Copied artifact changed: ' + str(target.relative_to(output)))
    for record in records:
        verify_record_public_call_logs(output, record)


def reject_evidence_symlinks(root: Path) -> None:
    """Never follow file or directory links while materializing evidence."""
    if root.is_symlink():
        raise ValueError('Evidence root must not be a symlink')
    if any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Evidence tree contains a symlink')


def materialize_reference_checks(output: Path, source: Path, batches: list[dict]) -> None:
    """Retain only hash-bound observations named by the execution certificates."""
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
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Reference check evidence differs from its certificate')
        target = contained_path(output, relative, reject_symlinks=True)
        if target.exists():
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Conflicting reference check evidence')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Copied reference check evidence changed')


def copy_cohort_evidence(source, target, dataset, records, batches, *,
                         summary_name, include_private=False, include_metadata=True):
    """Copy declared comparison evidence; publication excludes private envelopes."""
    if summary_name not in {'speed-comparison.json', 'resource-comparison.json'}:
        raise ValueError('Unsupported comparison summary')
    source = Path(source)
    reject_evidence_symlinks(source)
    source_root = source.resolve()

    allowed = set()
    if include_metadata:
        allowed.update({'dataset.json', 'results.jsonl', summary_name})
        for optional in ('execution.json', 'manifest.json', 'scoring-revisions.json',
                         'reference-revisions.json'):
            if (source / optional).is_file():
                allowed.add(optional)
        if include_private and (source / 'result-review.json').is_file():
            allowed.add('result-review.json')
    for record in records:
        verify_record_artifacts(source, record)
        allowed.update(relative for relative, _ in artifact_bindings(record))
        if include_private:
            allowed.update(binding['path'] for binding in record_private_bindings(record))
    for case in dataset.get('cases', []):
        case_id = case['id']
        source_dir = contained_path(source, 'sources/' + case_id)
        if source_dir.is_dir():
            allowed.update(child.relative_to(source_root).as_posix()
                           for child in source_dir.rglob('*') if child.is_file())
        reference = contained_path(source, 'list-references/' + case_id + '.json')
        if reference.is_file():
            allowed.add(reference.relative_to(source_root).as_posix())

    target.mkdir(parents=True)
    materialize_reference_checks(target, source, batches)
    for relative in sorted(allowed):
        source_file = contained_path(source, relative, reject_symlinks=True)
        if not source_file.is_file():
            raise ValueError('Comparison cohort allowlisted file is missing: ' + relative)
        target_file = contained_path(target, relative, reject_symlinks=True)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)

    for record in records:
        for relative, digest in artifact_bindings(record):
            if hashlib.sha256(contained_path(target, relative, reject_symlinks=True).read_bytes()).hexdigest() != digest:
                raise ValueError('Copied comparison artifact changed: ' + relative)
        verify_record_public_call_logs(target, record)
