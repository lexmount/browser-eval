"""Certify, apply and verify frozen latency cohorts without changing scored evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from harness.record_io import parse_jsonl

from lexbench_webfetch.paths import contained_path


def prior_results_path(root: Path) -> Path:
    if (root / "latency-corrections.json").is_file():
        validate_latency_correction(root)
        return contained_path(root, "prior-latency-results.jsonl")
    return contained_path(root, "results.jsonl")


def validate_latency_correction(root: Path) -> dict | None:
    receipt_path = root / "latency-corrections.json"
    if not receipt_path.is_file():
        return None
    receipt = json.loads(
        contained_path(root, "latency-corrections.json").read_text(encoding="utf-8")
    )
    prior_path = contained_path(root, "prior-latency-results.jsonl")
    current_path = contained_path(root, "results.jsonl")
    prior_bytes = prior_path.read_bytes()
    current_bytes = current_path.read_bytes()
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "webmainbench_moli_200_balanced_latency_only_v1"
        or receipt.get("prior_results_sha256") != hashlib.sha256(prior_bytes).hexdigest()
        or (receipt.get("output_results_sha256") != hashlib.sha256(current_bytes).hexdigest())
        or (receipt.get("selected_round") != 2)
        or (receipt.get("changed_result_count") != 400)
    ):
        raise ValueError("Invalid WebMainBench latency correction binding")
    previous = parse_jsonl(prior_bytes.decode("utf-8"), skip_blank=True)
    current = parse_jsonl(current_bytes.decode("utf-8"), skip_blank=True)
    if len(previous) != len(current) or len(current) != 1200:
        raise ValueError("WebMainBench latency correction changed result inventory")
    changes = receipt.get("changes")
    if not isinstance(changes, list) or len(changes) != 400:
        raise ValueError("WebMainBench latency correction has incomplete scope")
    by_attempt = {item.get("attempt_id"): item for item in changes}
    if len(by_attempt) != 400:
        raise ValueError("WebMainBench latency correction duplicated an attempt")
    expected = set()
    for old, new in zip(previous, current):
        if old.get("attempt_id") != new.get("attempt_id"):
            raise ValueError("WebMainBench latency correction reordered results")
        changed = old.get("provider") in ("moli", "moli-1.1.7")
        if changed:
            expected.add(old["attempt_id"])
            before_metadata = old.get("provider_metadata", {})
            after_metadata = new.get("provider_metadata", {})
            correction = after_metadata.get("latency_correction", {})
            change = by_attempt.get(old["attempt_id"], {})
            if (
                correction.get("kind") != "certified_balanced_final_round"
                or correction.get("selected_round") != 2
                or correction.get("original_elapsed_ms") != old["elapsed_ms"]
                or (correction.get("corrected_elapsed_ms") != new["elapsed_ms"])
                or (change.get("before_ms") != old["elapsed_ms"])
                or (change.get("after_ms") != new["elapsed_ms"])
                or (change.get("attempt_id") != old["attempt_id"])
                or (
                    {
                        key: value
                        for key, value in after_metadata.items()
                        if key != "latency_correction"
                    }
                    != before_metadata
                )
            ):
                raise ValueError("WebMainBench latency correction metadata mismatch")
            before = {key: value for key, value in old.items() if key != "elapsed_ms"}
            after = {
                key: value
                for key, value in new.items()
                if key not in ("elapsed_ms", "provider_metadata")
            }
            before.pop("provider_metadata", None)
            if before != after:
                raise ValueError("WebMainBench latency correction changed scored evidence")
        elif old != new:
            raise ValueError("WebMainBench latency correction changed another provider")
    if set(by_attempt) != expected:
        raise ValueError("WebMainBench latency correction omitted a Moli result")
    return receipt


ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind_certifier_source(directory: Path, source: Path, frozen: bytes) -> dict:
    """Retain the pre-call source bytes, refusing a mid-run source change."""
    if source.read_bytes() != frozen:
        raise ValueError("Certifier source changed during latency calls")
    target = contained_path(directory, "certifier-source.py", reject_symlinks=True)
    with target.open("xb") as stream:
        stream.write(frozen)
    return {"path": target.name, "sha256": hashlib.sha256(frozen).hexdigest()}


def verify_certifier_source(directory: Path, exclusion: dict) -> None:
    """Verify frozen provenance without executing it or consulting a live entry."""
    expected = exclusion.get("certifying_runner_sha256")
    if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
        raise ValueError("Invalid latency certifier source hash")
    binding = exclusion.get("certifying_runner_source")
    if binding is None:
        data = Path(__file__).parent / "data"
        registry = json.loads(
            (data / "webmainbench-latency-source.json").read_bytes()
        )
        sources = [row for row in registry["sources"] if row["sha256"] == expected]
        if (
            registry.get("schema_version") != 1
            or registry.get("kind") != "frozen_webmainbench_latency_certifier"
            or len(sources) != 1
        ):
            raise ValueError("Unrecognized historical latency certifier source")
        artifact = contained_path(data, sources[0]["source_artifact"], reject_symlinks=True)
        if digest(artifact) != expected:
            raise ValueError("Historical latency certifier source changed")
        return
    if (
        not isinstance(binding, dict)
        or set(binding) != {"path", "sha256"}
        or binding["sha256"] != expected
    ):
        raise ValueError("Invalid latency certifier source binding")
    source = contained_path(directory, binding["path"], reject_symlinks=True)
    if digest(source) != expected:
        raise ValueError("Frozen latency certifier source changed")


def competing_benchmark_pids() -> list[int]:
    """Reject another browser benchmark sharing this host during timing calls."""
    listing = subprocess.run(
        ["ps", "-axo", "pid=,command="], check=True, capture_output=True, text=True
    ).stdout
    markers = (
        "-m harness.browser_use.runner",
        "-m harness.trajectory_replay.",
        "scripts/run_webmainbench_benchmark.py",
        "scripts/run_benchmark.py",
        "scripts/build_report.py",
        "scripts/check_report.py",
        "scripts/check_report_links.py",
    )
    processes = [line.strip().split(None, 1) for line in listing.splitlines() if line.strip()]
    return [
        int(pid)
        for pid, command in processes
        if not command.startswith(("zsh -c ", "bash -lc ", "/bin/sh -c "))
        and any(marker in command for marker in markers)
    ]


def host_sample() -> dict:
    top = subprocess.run(
        ["top", "-l", "1", "-n", "0"], capture_output=True, text=True, check=True
    ).stdout
    pressure = subprocess.run(
        ["memory_pressure", "-Q"], capture_output=True, text=True, check=True
    ).stdout

    def match(pattern: str, value: str) -> float | None:
        found = re.search(pattern, value)
        return float(found.group(1)) if found else None

    return {
        "cpu_idle_percent": match("([\\d.]+)% idle", top),
        "physical_unused_mb": match("PhysMem:.*?([\\d.]+)M unused", top),
        "memory_free_percent": match("System-wide memory free percentage:\\s*([\\d.]+)%", pressure),
    }


def snapshot(dataset: Path, sample: Path, official: Path) -> dict:
    from .webmainbench_runner import load_selected_ids, load_source

    selected, binding = load_selected_ids(sample)
    source = {row["track_id"]: row for row in load_source(dataset, selected)}
    recorded = json.loads((official / "dataset.json").read_text(encoding="utf-8"))
    cases = {row["id"]: row for row in recorded["cases"]}
    if (
        len(source) != 200
        or set(source) != set(cases)
        or binding["sha256"] != recorded["sample_manifest_sha256"]
    ):
        raise ValueError("Frozen WebMainBench scope changed")
    for case_id, row in source.items():
        if (
            hashlib.sha256(row["html"].encode()).hexdigest() != cases[case_id]["input_html_sha256"]
            or hashlib.sha256(row["groundtruth_content"].encode()).hexdigest()
            != cases[case_id]["reference_sha256"]
            or digest(official / "references" / f"{case_id}.md")
            != cases[case_id]["reference_sha256"]
        ):
            raise ValueError("Frozen input or human reference changed: " + case_id)
    return {
        "source_dataset_sha256": digest(dataset),
        "sample_sha256": digest(sample),
        "official_dataset_sha256": digest(official / "dataset.json"),
        "reference_sha256": {
            case_id: cases[case_id]["reference_sha256"] for case_id in sorted(cases)
        },
        "input_html_sha256": {
            case_id: cases[case_id]["input_html_sha256"] for case_id in sorted(cases)
        },
    }


def validate_host_sample(sample: dict) -> None:
    if not isinstance(sample, dict):
        raise ValueError("Invalid latency host-resource observation")
    for metric in ("cpu_idle_percent", "memory_free_percent"):
        value = sample.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError("Invalid latency host-resource observation: " + metric)


def certify(argv: list[str] | None = None) -> None:
    from .webmainbench_latency_diagnostic import _repeat_calls

    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "older",
        "newer",
        "prior-older",
        "prior-newer",
        "dataset",
        "sample",
        "official-evidence",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("Certified output directory already exists")
    source_path = Path(__file__)
    frozen_source = source_path.read_bytes()
    competing = competing_benchmark_pids()
    if competing:
        raise ValueError(
            f"Concurrent browser benchmark is active (PIDs {competing}); "
            "do not collect comparable latency now"
        )
    clean_process_checks = 1
    before_host = host_sample()
    validate_host_sample(before_host)
    before = snapshot(args.dataset, args.sample, args.official_evidence)
    command = [
        sys.executable,
        "-m",
        "harness.web_fetch.webmainbench_latency_diagnostic",
        "--older",
        str(args.older),
        "--newer",
        str(args.newer),
        "--prior-older",
        str(args.prior_older),
        "--prior-newer",
        str(args.prior_newer),
        "--dataset",
        str(args.dataset),
        "--sample",
        str(args.sample),
        "--output",
        str(args.output),
        "--all-cases",
        "--rounds",
        "2",
        "--formal-correction",
    ]
    process = subprocess.Popen(command, cwd=ROOT)
    while process.poll() is None:
        time.sleep(2)
        competing = competing_benchmark_pids()
        if competing:
            process.terminate()
            process.wait(timeout=10)
            (args.output / "environment-interference.json").write_text(
                json.dumps({"kind": "concurrent_benchmark", "pids": competing}, indent=2) + "\n"
            )
            raise ValueError(
                f"Concurrent browser benchmark started during Moli timing: {competing}"
            )
        clean_process_checks += 1
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)
    after_host = host_sample()
    validate_host_sample(after_host)
    after = snapshot(args.dataset, args.sample, args.official_evidence)
    if before != after:
        raise ValueError("Source or references changed during Moli calls")
    selection = json.loads((args.output / "selection.json").read_text(encoding="utf-8"))
    expected_prior = {"1.1.5": digest(args.prior_older), "1.1.7": digest(args.prior_newer)}
    expected_binaries = {"1.1.5": digest(args.older), "1.1.7": digest(args.newer)}
    case_rows = {
        case_id: {
            "input_html_sha256": value,
            "reference_sha256": before["reference_sha256"][case_id],
        }
        for case_id, value in before["input_html_sha256"].items()
    }
    binding = {
        "directory": args.output.name,
        "selection_json_sha256": digest(args.output / "selection.json"),
        "calls_jsonl_sha256": digest(args.output / "calls.jsonl"),
    }
    _, calls = _repeat_calls(
        args.output.parent,
        binding,
        case_rows,
        before["source_dataset_sha256"],
        before["sample_sha256"],
        expected_prior,
        expected_binaries,
        200,
        2,
    )
    authority = {
        "selected_round": 2,
        "rule": "final_round_for_every_version_and_page; never select a fastest call",
        "scope": "latency only; preserve original output, scoring and verdict",
    }
    if selection.get("authority") != authority:
        raise ValueError("Final-round authority was not declared before calls")
    failures = [row for row in calls if not row["_body_present"]]
    if (
        len(failures) != 4
        or len({row["case_id"] for row in failures}) != 1
        or any("langrensha.163.com/wanfa/guize/" not in row["stderr"] for row in failures)
    ):
        raise ValueError("Corrected cohort failure scope changed")
    source_binding = bind_certifier_source(args.output, source_path, frozen_source)
    receipt = {
        "kind": "certified_webmainbench_moli_latency_crossover_v1",
        "precheck": before,
        "postcheck": after,
        "selection_sha256": binding["selection_json_sha256"],
        "calls_sha256": binding["calls_jsonl_sha256"],
        "physical_calls": len(calls),
        "failed_calls": len(failures),
        "selected_round": 2,
        "versions": expected_binaries,
        "prior_results_sha256": expected_prior,
        "environment_exclusion": {
            "rule": "no other browser benchmark process before or during Moli timing",
            "clean_process_checks": clean_process_checks,
            "certifying_runner_sha256": source_binding["sha256"],
            "certifying_runner_source": source_binding,
        },
        "host_resources": {"before": before_host, "after": after_host},
    }
    (args.output / "certification.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    print("Certified WebMainBench Moli latency cohort:", args.output)


def main(argv: list[str] | None = None) -> None:
    from .webmainbench_latency_diagnostic import _repeat_calls

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "cohort", "dataset", "sample", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or (args.cohort / "environment-interference.json").exists():
        raise ValueError("Output already exists or cohort is known to have host interference")
    cert_path = args.cohort / "certification.json"
    cert = json.loads(cert_path.read_text(encoding="utf-8"))
    selection_bytes = (args.cohort / "selection.json").read_bytes()
    exclusion = cert.get("environment_exclusion", {})
    verify_certifier_source(args.cohort, exclusion)
    resources = cert.get("host_resources", {})
    for point in ("before", "after"):
        validate_host_sample(resources.get(point))
    frozen = snapshot(args.dataset, args.sample, args.base)
    if (
        cert.get("kind") != "certified_webmainbench_moli_latency_crossover_v1"
        or cert.get("precheck") != frozen
        or cert.get("postcheck") != frozen
        or (cert.get("physical_calls") != 800)
        or (cert.get("failed_calls") != 4)
        or (cert.get("selected_round") != 2)
        or (
            exclusion.get("rule")
            != "no other browser benchmark process before or during Moli timing"
        )
        or type(exclusion.get("clean_process_checks")) is not int
        or exclusion["clean_process_checks"] < 2
        or (cert.get("selection_sha256") != hashlib.sha256(selection_bytes).hexdigest())
        or (cert.get("calls_sha256") != digest(args.cohort / "calls.jsonl"))
    ):
        raise ValueError("Moli latency cohort is not certified against the formal references")
    selection = json.loads(selection_bytes)
    if selection.get("authority") != {
        "selected_round": 2,
        "rule": "final_round_for_every_version_and_page; never select a fastest call",
        "scope": "latency only; preserve original output, scoring and verdict",
    }:
        raise ValueError("Final-round timing authority was not declared before calls")
    dataset = json.loads((args.base / "dataset.json").read_text())
    cases = {case["id"]: case for case in dataset["cases"]}
    _, calls = _repeat_calls(
        args.cohort.parent,
        {
            "directory": args.cohort.name,
            "selection_json_sha256": cert["selection_sha256"],
            "calls_jsonl_sha256": cert["calls_sha256"],
        },
        cases,
        frozen["source_dataset_sha256"],
        frozen["sample_sha256"],
        cert["prior_results_sha256"],
        cert["versions"],
        200,
        2,
    )
    if set(selection["case_ids"]) != set(cases):
        raise ValueError("Latency correction omitted a frozen page")
    order = {}
    for first, second in zip(calls[::2], calls[1::2]):
        if (
            first["case_id"] != second["case_id"]
            or first["round"] != second["round"]
            or first["version"] == second["version"]
        ):
            raise ValueError("Moli versions were not called serially on each page")
        order.setdefault(first["case_id"], []).append(first["version"])
    if any(sorted(versions) != ["1.1.5", "1.1.7"] for versions in order.values()):
        raise ValueError("Moli first-call order was not reversed on every page")
    final = {(row["version"], row["case_id"]): row for row in calls if row["round"] == 2}
    if len(final) != 400:
        raise ValueError("Moli final round is incomplete")
    prior_path = args.base / "results.jsonl"
    old_bytes = prior_path.read_bytes()
    records = parse_jsonl(old_bytes, skip_blank=True)
    changes = []
    for record in records:
        version = {"moli": "1.1.5", "moli-1.1.7": "1.1.7"}.get(record["provider"])
        if version is None:
            continue
        call = final[version, record["case_id"]]
        if bool(record["ok"]) != call["_body_present"]:
            raise ValueError("Corrected call changed success population: " + record["attempt_id"])
        original = record["elapsed_ms"]
        metadata = record.setdefault("provider_metadata", {})
        if metadata.get("binary_sha256") != cert["versions"][version]:
            raise ValueError("Corrected call changed Moli binary: " + record["attempt_id"])
        record["elapsed_ms"] = call["elapsed_ms"]
        metadata["latency_correction"] = {
            "kind": "certified_balanced_final_round",
            "selected_round": 2,
            "original_elapsed_ms": original,
            "corrected_elapsed_ms": call["elapsed_ms"],
            "cohort_call_output_sha256": call["output_sha256"],
            "content_byte_identical": call["output_sha256"] == record["output_sha256"],
        }
        changes.append(
            {
                "attempt_id": record["attempt_id"],
                "before_ms": original,
                "after_ms": call["elapsed_ms"],
            }
        )
    if len(changes) != 400:
        raise ValueError("Latency correction did not cover both complete Moli methods")
    shutil.copytree(args.base, args.output)
    (args.output / "prior-latency-results.jsonl").write_bytes(old_bytes)
    corrected = "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in records).encode()
    (args.output / "results.jsonl").write_bytes(corrected)
    receipt = {
        "schema_version": 1,
        "kind": "webmainbench_moli_200_balanced_latency_only_v1",
        "selection": "all 200 pages, both Moli versions, final round of the balanced crossover",
        "selected_round": 2,
        "changed_result_count": 400,
        "prior_results_sha256": hashlib.sha256(old_bytes).hexdigest(),
        "output_results_sha256": hashlib.sha256(corrected).hexdigest(),
        "certification_sha256": digest(cert_path),
        "selection_sha256": cert["selection_sha256"],
        "calls_sha256": cert["calls_sha256"],
        "scored_outputs_changed": 0,
        "verdicts_changed": 0,
        "changes": changes,
    }
    (args.output / "latency-corrections.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    validate_latency_correction(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "changed": len(changes),
                "byte_identical_rerun_outputs": sum(
                    row["provider_metadata"]["latency_correction"]["content_byte_identical"]
                    for row in records
                    if row["provider"] in ("moli", "moli-1.1.7")
                ),
            },
            ensure_ascii=False,
        )
    )
