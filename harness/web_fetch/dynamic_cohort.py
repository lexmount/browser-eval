"""Append a complete independently measured dynamic cohort; never replace attempts."""

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from harness.record_io import parse_jsonl
from harness.web_fetch.artifacts import (
    materialize_bound_artifacts,
    materialize_reference_checks,
    verify_record_artifacts,
)
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.publication_privacy import project_execution_manifest
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.render_evidence import validate_render_reference
from harness.web_fetch.safety import commit_staged_evidence, reject_evidence_symlinks, validate_report_ids
from lexbench_webfetch.paths import contained_path
from harness.web_fetch.storage import current_evidence_dir, data_home


def frozen_case(case):
    """Bind results to identity, scope and scoring, not later attestations."""
    annotations = {
        "description",
        "tags",
        "ground_truth_evidence",
        "source_review_url",
        "source_verification",
        "rule_review",
        "source_benchmark",
    }
    frozen = {
        field.name: case.get(field.name)
        for field in fields(EvalCase)
        if field.name not in annotations
    }
    frozen["source_benchmark"] = {
        key: value
        for key, value in case.get("source_benchmark", {}).items()
        if key != "render_evidence"
    }
    return frozen


def cases_by_id(dataset):
    cases = {case["id"]: case for case in dataset["cases"]}
    if not cases or len(cases) != len(dataset["cases"]):
        raise ValueError("Empty or duplicate supplement cases")
    return cases


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed
    except (AttributeError, TypeError, ValueError):
        pass
    raise ValueError("Invalid batch or attempt timestamp (timezone required)")


def measured_batches(measured, records, reference_cases, previous, execution):
    """Validate the original manifests as a partition, without rewriting provenance."""
    path = measured / "execution.json"
    if path.exists():
        batches = json.loads(path.read_text()).get("batches")
    else:
        batches = [json.loads((measured / "manifest.json").read_text())]
    if not isinstance(batches, list) or not batches:
        raise ValueError("Invalid or empty supplement execution batches")
    require_verified_reference_checks(
        batches, cases=reference_cases, records=records, source=measured
    )
    by_attempt = {r["attempt_id"]: r for r in records}
    if len(by_attempt) != len(records) or by_attempt.keys() & {r["attempt_id"] for r in previous}:
        raise ValueError("Supplement attempt identity or manifest mismatch")
    seen, providers = set(), set()
    batch_ids = {b["batch_id"] for b in execution["batches"] if "batch_id" in b}
    for batch in batches:
        if not isinstance(batch, dict):
            raise ValueError("Invalid supplement batch manifest")
        if "batch_id" in batch:
            batch_id = batch["batch_id"]
            if not isinstance(batch_id, str) or not batch_id or batch_id in batch_ids:
                raise ValueError("Empty or duplicate batch ID")
            batch_ids.add(batch_id)
        entries = batch.get("providers")
        if (
            not isinstance(entries, list)
            or not entries
            or any(
                not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]
                for p in entries
            )
        ):
            raise ValueError("Empty or invalid supplement providers")
        settings = {p["name"]: p for p in entries}
        if len(settings) != len(entries):
            raise ValueError("Duplicate supplement providers")
        ids = batch.get("attempt_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids)
            or seen.intersection(ids)
            or not set(ids) <= by_attempt.keys()
            or type(batch.get("attempt_count")) is not int
            or batch["attempt_count"] != len(ids)
        ):
            raise ValueError("Supplement attempt partition or manifest mismatch")
        selected = [by_attempt[i] for i in ids]
        case_ids = {r["case_id"] for r in selected}
        case_count = batch.get("dataset", {}).get("case_count")
        if (
            type(case_count) is not int
            or case_count != len(case_ids)
            or not case_ids <= reference_cases.keys()
        ):
            raise ValueError("Batch dataset case_count mismatch")
        if (
            {r["provider"] for r in selected} != settings.keys()
            or len(selected) != len(case_ids) * len(settings)
            or {(r["case_id"], r["provider"]) for r in selected}
            != {(c, p) for c in case_ids for p in settings}
        ):
            raise ValueError("Batch provider membership or case coverage mismatch")
        runs = batch.get("parameters", {}).get("runs", 1)
        if type(runs) is not int or runs != 1:
            raise ValueError("Supplement must measure each case/provider exactly once")
        # Current runners supply batch boundaries and attempt start/duration,
        # not per-attempt completion timestamps or batch IDs. Check only the
        # metadata supplied, but never accept a started, unfinished batch.
        timed = (
            "started_at" in batch
            or "completed_at" in batch
            or any("started_at" in r or "elapsed_ms" in r for r in selected)
        )
        if timed:
            start, end = timestamp(batch.get("started_at")), timestamp(batch.get("completed_at"))
            if end < start:
                raise ValueError("Batch completed_at precedes started_at")
        for record in selected:
            provider = settings[record["provider"]]
            case = reference_cases[record["case_id"]]
            for field, expected in (
                ("case_name", case.get("name")),
                ("url", case.get("url")),
                ("category", case.get("category")),
            ):
                if field in record and record[field] != expected:
                    raise ValueError("Attempt differs from frozen reference identity")
            if (
                "provider_type" in record
                and record["provider_type"] != provider.get("type")
                or "batch_id" in record
                and record["batch_id"] != batch.get("batch_id")
            ):
                raise ValueError("Attempt provider or batch identity mismatch")
            interval = record.get("provider_metadata", {}).get("configured_min_interval_seconds")
            if interval is not None and interval != provider.get("min_interval_seconds"):
                raise ValueError("Attempt provider settings mismatch")
            if "run" in record and (type(record["run"]) is not int or record["run"] != runs):
                raise ValueError("Attempt run mismatch")
            if "started_at" in record or "elapsed_ms" in record:
                began = timestamp(record.get("started_at"))
                elapsed = record.get("elapsed_ms")
                if (
                    type(elapsed) not in (int, float)
                    or not math.isfinite(elapsed)
                    or elapsed < 0
                    or not start <= began <= end
                    or elapsed > (end - began).total_seconds() * 1000 + 0.01
                ):
                    raise ValueError("Attempt timing outside completed batch")
            if "completed_at" in record:
                finished = timestamp(record["completed_at"])
                began = timestamp(record.get("started_at"))
                if not start <= began <= finished <= end:
                    raise ValueError("Attempt completion outside completed batch")
        seen.update(ids)
        providers.update(settings)
    if seen != by_attempt.keys():
        raise ValueError("Supplement batch attempt partition is incomplete")
    expected_pairs = {(c, p) for c in reference_cases for p in providers}
    if (
        len(records) != len(expected_pairs)
        or {(r["case_id"], r["provider"]) for r in records} != expected_pairs
    ):
        raise ValueError("Incomplete or duplicate supplement attempts")
    return batches, providers


def append(source, reference, measured, checks):
    source, reference, measured = map(Path, (source, reference, measured))
    # copytree follows links by default; reject them before any evidence reads.
    for root in (source, reference, measured):
        reject_evidence_symlinks(root)
    dataset = json.loads((source / "dataset.json").read_text())
    supplement = json.loads((reference / "dataset.json").read_text())
    incoming_bytes = (measured / "results.jsonl").read_bytes()
    records = parse_jsonl(incoming_bytes)
    previous_bytes = (source / "results.jsonl").read_bytes()
    previous = parse_jsonl(previous_bytes)
    validate_report_ids(supplement, records)
    reference_cases = cases_by_id(supplement)
    if any(c.get("track") != "dynamic" for c in reference_cases.values()):
        raise ValueError("Supplement cases must belong to the dynamic track")
    new_ids = set(reference_cases)
    if new_ids & {c["id"] for c in dataset["cases"]}:
        raise ValueError("Supplement already present; refusing replacement")
    measured_cases = cases_by_id(json.loads((measured / "dataset.json").read_text()))
    if {key: frozen_case(case) for key, case in reference_cases.items()} != {
        key: frozen_case(case) for key, case in measured_cases.items()
    }:
        raise ValueError("Measured dataset differs from frozen reference identity or scoring")
    execution = json.loads((source / "execution.json").read_text())
    if not isinstance(execution.get("batches"), list):
        raise ValueError("Invalid execution batches")
    batches, providers = measured_batches(measured, records, reference_cases, previous, execution)
    guarded_ids = {cid for b in batches for cid in b.get("reference_checks", {}).get("cases", {})}
    coverage_plan = dataset.setdefault("coverage_plan", {})
    planned = coverage_plan.get("dynamic")
    if planned is not None:
        if (
            not isinstance(planned, list)
            or any(not isinstance(p, str) for p in planned)
            or len(set(planned)) != len(planned)
            or set(planned) != providers
        ):
            raise ValueError("Supplement providers differ from existing dynamic planned cohort")
    elif any(c.get("track") == "dynamic" for c in dataset["cases"]):
        raise ValueError("Existing dynamic cases require a planned provider cohort")
    audits = {}
    source_hashes = {}
    for c in supplement["cases"]:
        check = next(x for x in checks if x["id"] == c["id"])
        evidence_dir = contained_path(reference, f"sources/{c['id']}")
        evidence_files = {
            p.relative_to(evidence_dir): p.read_bytes()
            for p in evidence_dir.rglob("*")
            if p.is_file()
        }
        rendered = evidence_files[Path("rendered.txt")].decode("utf-8")
        if "\n\n".join(check["entries"]) != rendered or not check["satisfied"]:
            raise ValueError("Completion recheck differs from pre-run frozen evidence")
        original_render_evidence = c["source_benchmark"].get("render_evidence")
        c["source_benchmark"]["render_evidence"] = {
            "scope": c["source_benchmark"]["capture_scope"],
            "rendered_truncated": False,
            "completion": {
                "condition": (
                    f"{check['id']}: {len(check['entries'])} nonempty target records "
                    "with populated text fields; no interaction"
                ),
                "timeout_ms": check["timeout_ms"],
                "elapsed_ms": check["elapsed_ms"],
                "satisfied": True,
                "checked_at": check["checked_at"],
                "phase": "post-run independent reproduction of pre-run frozen reference",
            },
        }
        if c["id"] in guarded_ids:
            c["source_benchmark"]["render_evidence"] = original_render_evidence
        audit = validate_render_reference(c, evidence_files[Path("initial.html.txt")], rendered)
        if not audit["valid"]:
            raise ValueError(audit["errors"])
        evidence = json.loads(evidence_files[Path("evidence.json")])
        for stored, actual in (
            ("initial_sha256", "initial_html_sha256"),
            ("rendered_sha256", "rendered_text_sha256"),
            ("reference_sha256", "reference_sha256"),
        ):
            if evidence[stored] != audit[actual]:
                raise ValueError(f"Source evidence hash mismatch: {c['id']}/{stored}")
        if (
            evidence_files[Path("reference.txt")] != c["ground_truth_main_content"].encode()
            or c["source_verification"]["reference_sha256"] != audit["reference_sha256"]
        ):
            raise ValueError(f"Frozen reference hash mismatch: {c['id']}")
        audits[c["id"]] = audit
        source_hashes[c["id"]] = {
            path: hashlib.sha256(raw).hexdigest() for path, raw in evidence_files.items()
        }
    artifact_paths = [contained_path(source, r["artifact_path"]) for r in records]
    if len(set(artifact_paths)) != len(artifact_paths):
        raise ValueError("Duplicate supplement artifact paths")
    for r in records:
        if Path(r["artifact_path"]).parts[0] != "artifacts":
            raise ValueError("Output paths must be inside artifacts/")
        raw = contained_path(measured, r["artifact_path"])
        if hashlib.sha256(raw.read_bytes()).hexdigest() != r["metrics"]["content_sha256"]:
            raise ValueError("Output hash mismatch")
        if contained_path(source, r["artifact_path"]).exists():
            raise ValueError("Output collision")
        verify_record_artifacts(measured, r)
    for c in supplement["cases"]:
        target = source / "sources" / c["id"]
        if target.exists():
            for relative, expected_hash in source_hashes[c["id"]].items():
                original = contained_path(target, relative)
                if (
                    not original.is_file()
                    or hashlib.sha256(original.read_bytes()).hexdigest() != expected_hash
                ):
                    raise ValueError("Source evidence collision")
    dataset["cases"] += supplement["cases"]
    baseline_names = sorted({r["provider"] for r in previous})
    for track in {c.get("track", "web") for c in dataset["cases"]}:
        coverage_plan.setdefault(track, sorted(providers) if track == "dynamic" else baseline_names)
    execution["batches"].extend(batches)
    # Finish serialization and every input/collision check before staging or
    # touching canonical data. Scores are copied verbatim, never recomputed.
    outputs = {
        "dataset.json": (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "results.jsonl": previous_bytes
        + (b"\n" if previous_bytes and not previous_bytes.endswith(b"\n") else b"")
        + incoming_bytes,
        "execution.json": (json.dumps(execution, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        ),
    }
    transaction = Path(tempfile.mkdtemp(prefix=f".{source.name}-append-", dir=source.parent))
    staged, backup = transaction / "staged", transaction / "original"
    committed = False
    try:
        shutil.copytree(source, staged)
        materialize_reference_checks(staged, measured, batches)
        for c in supplement["cases"]:
            target = staged / "sources" / c["id"]
            if not target.exists():
                shutil.copytree(reference / "sources" / c["id"], target)
            for relative, expected_hash in source_hashes[c["id"]].items():
                copied = contained_path(target, relative)
                if (
                    not copied.is_file()
                    or hashlib.sha256(copied.read_bytes()).hexdigest() != expected_hash
                ):
                    raise ValueError(f"Staged source evidence hash mismatch: {c['id']}/{relative}")
            (target / "render-audit.json").write_text(
                json.dumps(audits[c["id"]], ensure_ascii=False, indent=2) + "\n"
            )
        materialize_bound_artifacts(staged, measured, records)
        for name, content in outputs.items():
            (staged / name).write_bytes(content)
        commit_staged_evidence(source, staged, backup)
        committed = True
    finally:
        # Never delete the sole original if rollback itself failed.
        if committed or not backup.exists():
            shutil.rmtree(transaction, ignore_errors=True)
    print(
        f"Appended {len(new_ids)} cases, {len(records)} measured attempts; "
        "baseline records preserved"
    )


def merge(reference, runs, output):
    reference, output = Path(reference), Path(output)
    runs = [Path(run) for run in runs]
    if not runs:
        raise ValueError("At least one measured run is required")
    for root in (reference, *runs):
        reject_evidence_symlinks(root)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if any(output.resolve().is_relative_to(root.resolve()) for root in (reference, *runs)):
        raise ValueError("Output must not be nested inside its input evidence")
    dataset = json.loads((reference / "dataset.json").read_bytes())
    reference_cases = cases_by_id(dataset)
    expected = {key: frozen_case(case) for key, case in reference_cases.items()}
    records, batches, primary_paths, sources = [], [], set(), []
    for run in runs:
        actual = json.loads((run / "dataset.json").read_bytes())
        if expected != {key: frozen_case(case) for key, case in cases_by_id(actual).items()}:
            raise ValueError("Run differs from frozen cases")
        raw_records = (run / "results.jsonl").read_bytes()
        rows = parse_jsonl(raw_records)
        validate_report_ids(dataset, rows)
        for row in rows:
            path = contained_path(run, row["artifact_path"], reject_symlinks=True)
            normalized = path.relative_to(run.resolve()).as_posix()
            if (
                normalized in primary_paths
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != row["metrics"]["content_sha256"]
            ):
                raise ValueError("Duplicate artifact or hash mismatch")
            primary_paths.add(normalized)
            verify_record_artifacts(run, row)
        original_batches = json.loads((run / "execution.json").read_bytes())["batches"]
        require_verified_reference_checks(
            original_batches, cases=reference_cases, records=rows, source=run
        )
        batches.extend(project_execution_manifest(batch) for batch in original_batches)
        sources.append((run, rows, original_batches, raw_records))
        records.extend(rows)
    validate_report_ids(dataset, records)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-merge-", dir=output.parent) as work:
        staged = Path(work) / "evidence"
        staged.mkdir()
        for run, rows, original_batches, _ in sources:
            materialize_bound_artifacts(staged, run, rows)
            materialize_reference_checks(staged, run, original_batches)
        for filename, value in [
            ("dataset.json", dataset),
            ("execution.json", {"batches": batches}),
        ]:
            (staged / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        (staged / "results.jsonl").write_bytes(
            b"".join(
                raw + (b"\n" if raw and not raw.endswith(b"\n") else b"")
                for _, _, _, raw in sources
            )
        )
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        staged.rename(output)
    print(f"Assembled {len(records)} unchanged measurements in {len(batches)} original batches")


def main_append(argv=None):
    parser = argparse.ArgumentParser(description="Append a complete measured dynamic cohort")
    parser.add_argument("--source", type=Path, default=current_evidence_dir())
    parser.add_argument(
        "--reference", type=Path, default=data_home() / "runs/dynamic-content-reference"
    )
    parser.add_argument(
        "--measured", type=Path, default=data_home() / "runs/dynamic-content-measured"
    )
    parser.add_argument(
        "--checks",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "research/dynamic-completion-checks.json",
    )
    args = parser.parse_args(argv)
    append(args.source, args.reference, args.measured, json.loads(args.checks.read_bytes()))


def main_merge(argv=None):
    parser = argparse.ArgumentParser(
        description="Assemble measured dynamic batches without provider calls"
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    merge(args.reference, args.runs, args.output)
