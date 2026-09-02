"""Add a complete new provider run without changing existing measured evidence."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .safety import contained_path, safe_identifier, validate_report_ids


def read_records(root):
    return [json.loads(line) for line in (root / "results.jsonl").read_text().splitlines()]


def add_provider_run(canonical: Path, incoming: Path, provider: str, implementation=None):
    canonical, incoming = canonical.resolve(), incoming.resolve()
    safe_identifier(provider)
    dataset = json.loads((canonical / "dataset.json").read_text())
    if json.loads((incoming / "dataset.json").read_text()) != dataset:
        raise ValueError("Incoming dataset/reference differs from the canonical dataset")
    existing, records = read_records(canonical), read_records(incoming)
    validate_report_ids(dataset, existing + records)
    cases = {c["id"] for c in dataset["cases"]}
    if any(r["provider"] == provider for r in existing):
        raise ValueError("Provider already has canonical records; refusing replacement")
    if not records or any(r["provider"] != provider for r in records):
        raise ValueError("Incoming records must belong only to the requested provider")
    if Counter(r["case_id"] for r in records) != Counter({c: 1 for c in cases}):
        raise ValueError("Incoming run must cover every case exactly once")
    identifiers = [r["attempt_id"] for r in existing + records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate attempt IDs")
    paths = [r["artifact_path"] for r in existing + records]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate artifact paths")
    for root, group in [(canonical, existing), (incoming, records)]:
        for r in group:
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
        original = (canonical / "results.jsonl").read_bytes()
        if original and not original.endswith(b"\n"):
            raise ValueError("Canonical JSONL must end with newline")
        # Existing lines are copied byte-for-byte, not reserialized or rescored.
        (staging / "results.jsonl").write_bytes(
            original + (incoming / "results.jsonl").read_bytes()
        )
        (staging / "execution.json").write_text(
            json.dumps(execution, ensure_ascii=False, indent=2) + "\n"
        )
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
