"""Add a complete new provider run without changing existing measured evidence."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .safety import contained_path, safe_identifier, validate_report_ids
from .artifacts import materialize_private_artifacts, verify_record_private_artifacts


def parse_records(raw):
    return [json.loads(line) for line in raw.splitlines()]


def add_provider_run(canonical: Path, incoming: Path, provider: str, implementation=None, *, track=None):
    canonical, incoming = canonical.resolve(), incoming.resolve()
    safe_identifier(provider)
    dataset = json.loads((canonical / "dataset.json").read_text())
    incoming_dataset = json.loads((incoming / "dataset.json").read_text())
    selected = [c for c in dataset['cases'] if track is None or c.get('track', 'web') == track]
    if not selected:
        raise ValueError('Requested track has no canonical cases')
    if track is None:
        matches = incoming_dataset == dataset
    else:
        matches = (incoming_dataset.get('tool') == dataset.get('tool')
                   and incoming_dataset.get('cases') == selected)
    if not matches:
        raise ValueError("Incoming dataset/reference differs from the canonical dataset")
    # Validate and publish the same byte snapshots, even if incoming files are
    # replaced during staging. Never re-read unvalidated JSONL for publication.
    original = (canonical / "results.jsonl").read_bytes()
    incoming_bytes = (incoming / "results.jsonl").read_bytes()
    if original and not original.endswith(b"\n"):
        raise ValueError("Canonical JSONL must end with newline")
    existing, records = parse_records(original), parse_records(incoming_bytes)
    validate_report_ids(dataset, existing + records)
    cases = {c["id"] for c in selected}
    if any(r["provider"] == provider and r['case_id'] in cases for r in existing):
        raise ValueError("Provider already has canonical records in this scope; refusing replacement")
    if not records or any(r["provider"] != provider for r in records):
        raise ValueError("Incoming records must belong only to the requested provider")
    if Counter(r["case_id"] for r in records) != Counter({c: 1 for c in cases}):
        raise ValueError("Incoming run must cover every case exactly once")
    identifiers = [r["attempt_id"] for r in existing + records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate attempt IDs")
    paths = [contained_path(canonical, r["artifact_path"]) for r in existing + records]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate artifact paths")
    for root, group in [(canonical, existing), (incoming, records)]:
        for r in group:
            verify_record_private_artifacts(root, r)
            if Path(r['artifact_path']).parts[0] != 'artifacts':
                raise ValueError('Output paths must be inside artifacts/')
            artifact = contained_path(root, r["artifact_path"])
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != r["metrics"]["content_sha256"]:
                raise ValueError("Output hash mismatch: " + r["attempt_id"])
    if implementation and implementation.get("sha256"):
        if any(
            r["provider_metadata"].get("binary_sha256") != implementation["sha256"] for r in records
        ):
            raise ValueError("Executable fingerprint mismatch")
    batches = deepcopy(json.loads((incoming / "execution.json").read_text())["batches"])
    assigned = []
    for batch in batches:
        if not batch.get("completed_at"):
            raise ValueError("Incoming batch is unfinished")
        names = {p["name"] for p in batch["providers"]}
        selected = [
            r
            for r in records
            if r["provider"] in names
            and batch["started_at"] <= r["started_at"] <= batch["completed_at"]
        ]
        if len(selected) != batch["attempt_count"]:
            raise ValueError("Batch attempt count does not match measured records")
        batch["attempt_ids"] = [r["attempt_id"] for r in selected]
        assigned.extend(batch["attempt_ids"])
        if implementation:
            batch["implementation"] = deepcopy(implementation)
    if Counter(assigned) != Counter(r["attempt_id"] for r in records):
        raise ValueError("Each new attempt must belong to exactly one execution batch")
    execution = json.loads((canonical / "execution.json").read_text())
    execution["batches"].extend(batches)
    if track is not None:
        plan = dataset.get('coverage_plan')
        if not isinstance(plan, dict) or track not in plan:
            raise ValueError('Track supplement requires an explicit canonical coverage plan')
        plan[track] = sorted(set(plan[track]) | {provider})
    # Validate everything before constructing a replacement evidence tree.
    for child in canonical.rglob("*"):
        if child.is_symlink():
            raise ValueError("Canonical evidence must not contain symlinks")
    staging = Path(tempfile.mkdtemp(prefix=".evidence-add-", dir=canonical.parent))
    swap = None
    try:
        shutil.copytree(canonical, staging, dirs_exist_ok=True)
        for r in records:
            target = contained_path(staging, r["artifact_path"])
            if target.exists():
                raise ValueError("Refusing to overwrite an existing artifact")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(contained_path(incoming, r["artifact_path"]), target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != r['metrics']['content_sha256']:
                raise ValueError('Staged output hash mismatch')
        materialize_private_artifacts(staging, incoming, records)
        # Existing lines are copied byte-for-byte, not reserialized or rescored.
        (staging / "results.jsonl").write_bytes(
            original + incoming_bytes
        )
        (staging / "execution.json").write_text(
            json.dumps(execution, ensure_ascii=False, indent=2) + "\n"
        )
        if track is not None:
            (staging / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n')
        swap = Path(tempfile.mkdtemp(prefix=".evidence-swap-", dir=canonical.parent))
        canonical.rename(swap / "original")
        try:
            staging.rename(canonical)
        except BaseException:
            (swap / "original").rename(canonical)
            raise
        shutil.rmtree(swap)
        return len(records)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if swap and swap.exists() and not (swap / "original").exists():
            shutil.rmtree(swap)
