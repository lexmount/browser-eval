"""Frozen latency certificates survive entry-point changes without executing evidence."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.web_fetch import webmainbench_latency_correction as latency
from harness.web_fetch import webmainbench_runner


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    base, calls_root = tmp_path / "base", tmp_path / "cohort"
    base.mkdir()
    calls_root.mkdir()
    source = tmp_path / "source.jsonl"
    sample = tmp_path / "sample.txt"
    ids = [f"case-{i:03}" for i in range(200)]
    sample.write_text("\n".join(ids))
    source_rows = [
        {
            "track_id": f"case-{i:03}",
            "html": f"<p>Input {i}</p>",
            "groundtruth_content": f"Reference {i}",
        }
        for i in range(545)
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in source_rows))
    original_loader = webmainbench_runner.load_source
    monkeypatch.setattr(
        webmainbench_runner,
        "load_source",
        lambda path, selected: original_loader(
            path, selected, expected_sha256=sha(source.read_bytes())
        ),
    )
    definitions = []
    for row in source_rows[:200]:
        case_id = row["track_id"]
        reference = base / "references" / f"{case_id}.md"
        reference.parent.mkdir(exist_ok=True)
        reference.write_text(row["groundtruth_content"])
        definitions.append(
            {
                "id": case_id,
                "input_html_sha256": sha(row["html"].encode()),
                "reference_sha256": sha(reference.read_bytes()),
            }
        )
    write_json(
        base / "dataset.json",
        {"cases": definitions, "sample_manifest_sha256": sha(sample.read_bytes())},
    )
    binary = {"1.1.5": "a" * 64, "1.1.7": "b" * 64}
    providers = {"moli": "1.1.5", "moli-1.1.7": "1.1.7"}
    prior = []
    for case_id in ids:
        for provider in [*providers, "kitesurf", "lightpanda", "obscura", "chromium"]:
            body = b"" if case_id == ids[0] and provider in providers else b"Frozen body"
            prior.append(
                {
                    "attempt_id": f"{provider}-{case_id}",
                    "provider": provider,
                    "case_id": case_id,
                    "elapsed_ms": 100,
                    "ok": bool(body),
                    "output_sha256": sha(body),
                    "metrics": {"score": 0.5},
                    "provider_metadata": {
                        "binary_sha256": binary[providers[provider]]
                        if provider in providers
                        else "c" * 64
                    },
                }
            )
    (base / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in prior))
    prior_hashes = {version: sha((base / "results.jsonl").read_bytes()) for version in binary}
    calls = []
    for definition in definitions:
        case_id = definition["id"]
        for round_index in [1, 2]:
            for version in ["1.1.5", "1.1.7"] if round_index == 1 else ["1.1.7", "1.1.5"]:
                body = b"" if case_id == ids[0] else b"Frozen body"
                path = calls_root / "outputs" / str(round_index) / version / f"{case_id}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                calls.append(
                    {
                        "case_id": case_id,
                        "version": version,
                        "round": round_index,
                        "input_html_sha256": definition["input_html_sha256"],
                        "output_sha256": sha(body),
                        "output_bytes": len(body),
                        "exit_code": 0 if body else 1,
                        "elapsed_ms": 80 if version == "1.1.5" else 90,
                        "stderr": "" if body else "https://langrensha.163.com/wanfa/guize/",
                    }
                )
    (calls_root / "calls.jsonl").write_text("".join(json.dumps(row) + "\n" for row in calls))
    write_json(
        calls_root / "selection.json",
        {
            "case_ids": ids,
            "rounds": 2,
            "binaries": {version: {"sha256": value} for version, value in binary.items()},
            "source_dataset_sha256": sha(source.read_bytes()),
            "sample_sha256": sha(sample.read_bytes()),
            "prior_results_sha256": prior_hashes,
            "authority": {
                "selected_round": 2,
                "rule": "final_round_for_every_version_and_page; never select a fastest call",
                "scope": "latency only; preserve original output, scoring and verdict",
            },
        },
    )
    frozen = latency.snapshot(source, sample, base)
    cert = {
        "kind": "certified_webmainbench_moli_latency_crossover_v1",
        "precheck": frozen,
        "postcheck": frozen,
        "physical_calls": 800,
        "failed_calls": 4,
        "selected_round": 2,
        "versions": binary,
        "prior_results_sha256": prior_hashes,
        "selection_sha256": sha((calls_root / "selection.json").read_bytes()),
        "calls_sha256": sha((calls_root / "calls.jsonl").read_bytes()),
        "environment_exclusion": {
            "rule": "no other browser benchmark process before or during Moli timing",
            "clean_process_checks": 3,
        },
        "host_resources": {
            point: {"cpu_idle_percent": 90, "memory_free_percent": 70}
            for point in ["before", "after"]
        },
    }
    return base, calls_root, source, sample, cert, prior


@pytest.mark.parametrize("historical", [None, 0, 1])
def test_apply_complete_frozen_cohort_survives_entry_point_changes(cohort, tmp_path, historical):
    base, calls_root, source, sample, cert, prior = cohort
    if historical is not None:
        registry = json.loads(
            (Path(latency.__file__).parent / "data/webmainbench-latency-source.json").read_bytes()
        )
        binding = registry["sources"][historical]
        cert["environment_exclusion"]["certifying_runner_sha256"] = binding["sha256"]
        root = Path(__file__).resolve().parents[1]
        assert sha((root / binding["path"]).read_bytes()) != binding["sha256"]
    else:
        emitter = tmp_path / "emitter.py"
        marker = tmp_path / "executed"
        payload = (
            f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
            'raise RuntimeError("must not execute")\n'
        ).encode()
        emitter.write_bytes(payload)
        binding = latency.bind_certifier_source(calls_root, emitter, payload)
        cert["environment_exclusion"].update(
            certifying_runner_sha256=binding["sha256"], certifying_runner_source=binding
        )
        emitter.write_text("# changed current entry point\n")
    write_json(calls_root / "certification.json", cert)
    output = tmp_path / "corrected"
    latency.main(
        [
            "--base",
            str(base),
            "--cohort",
            str(calls_root),
            "--dataset",
            str(source),
            "--sample",
            str(sample),
            "--output",
            str(output),
        ]
    )
    receipt = latency.validate_latency_correction(output)
    assert receipt["changed_result_count"] == 400
    current = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    for old, new in zip(prior, current, strict=True):
        assert new["metrics"] == old["metrics"] and new["ok"] == old["ok"]
        assert new["output_sha256"] == old["output_sha256"]
        assert new["elapsed_ms"] == (
            80 if old["provider"] == "moli" else 90 if old["provider"] == "moli-1.1.7" else 100
        )
    assert not (tmp_path / "executed").exists()


def test_historical_source_registry_binds_exact_retained_bytes(monkeypatch, tmp_path):
    def no_subprocess(*args, **kwargs):
        raise AssertionError("Historical source validation must not invoke Git or archived code")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    data = Path(latency.__file__).parent / "data"
    registry = json.loads(
        (data / "webmainbench-latency-source.json").read_bytes()
    )
    for source in registry["sources"]:
        assert sha((data / source["source_artifact"]).read_bytes()) == source["sha256"]
        latency.verify_certifier_source(
            tmp_path, {"certifying_runner_sha256": source["sha256"]}
        )


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink", "outside"])
def test_historical_source_damage_is_rejected_before_using_a_certificate(
    tmp_path, monkeypatch, damage
):
    import shutil

    original = Path(latency.__file__).parent / "data"
    local = tmp_path / "library"
    shutil.copytree(original, local / "data")
    registry_path = local / "data/webmainbench-latency-source.json"
    registry = json.loads(registry_path.read_bytes())
    source = registry["sources"][0]
    artifact = local / "data" / source["source_artifact"]
    if damage == "missing":
        artifact.unlink()
    elif damage == "changed":
        artifact.write_bytes(artifact.read_bytes() + b"\n")
    else:
        external = tmp_path / "external.py.txt"
        external.write_bytes(artifact.read_bytes())
        if damage == "symlink":
            artifact.unlink()
            artifact.symlink_to(external)
        else:
            source["source_artifact"] = str(external)
            registry_path.write_text(json.dumps(registry))
    monkeypatch.setattr(latency, "__file__", str(local / "latency.py"))
    with pytest.raises((ValueError, FileNotFoundError)):
        latency.verify_certifier_source(tmp_path, {"certifying_runner_sha256": source["sha256"]})


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf")])
def test_invalid_timing_in_an_unselected_round_cannot_certify_a_complete_cohort(
    cohort, tmp_path, value
):
    base, calls_root, source, sample, cert, _ = cohort
    registry = json.loads(
        (Path(latency.__file__).parent / "data/webmainbench-latency-source.json").read_bytes()
    )
    cert["environment_exclusion"]["certifying_runner_sha256"] = registry["sources"][0]["sha256"]
    path = calls_root / "calls.jsonl"
    calls = [json.loads(line) for line in path.read_text().splitlines()]
    assert calls[0]["round"] == 1
    calls[0]["elapsed_ms"] = value
    path.write_text("".join(json.dumps(row) + "\n" for row in calls))
    cert["calls_sha256"] = sha(path.read_bytes())
    write_json(calls_root / "certification.json", cert)
    output = tmp_path / "corrected"
    with pytest.raises(ValueError, match="timing"):
        latency.main(
            [
                "--base",
                str(base),
                "--cohort",
                str(calls_root),
                "--dataset",
                str(source),
                "--sample",
                str(sample),
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


def test_changed_certifier_during_collection_is_not_frozen(tmp_path):
    source = tmp_path / "current.py"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed during latency calls"):
        latency.bind_certifier_source(tmp_path, source, b"before calls")
    assert not (tmp_path / "certifier-source.py").exists()


@pytest.mark.parametrize("damage", ["boolean-resource", "fractional-checks", "infinite-checks"])
def test_invalid_environment_observations_cannot_support_timing_correction(
    cohort, tmp_path, damage
):
    base, calls_root, source, sample, cert, _ = cohort
    registry = json.loads(
        (Path(latency.__file__).parent / "data/webmainbench-latency-source.json").read_bytes()
    )
    cert["environment_exclusion"]["certifying_runner_sha256"] = registry["sources"][0]["sha256"]
    if damage == "boolean-resource":
        cert["host_resources"]["before"]["cpu_idle_percent"] = True
    else:
        cert["environment_exclusion"]["clean_process_checks"] = (
            2.5 if damage == "fractional-checks" else float("inf")
        )
    write_json(calls_root / "certification.json", cert)
    output = tmp_path / "corrected"
    with pytest.raises(ValueError):
        latency.main(
            [
                "--base",
                str(base),
                "--cohort",
                str(calls_root),
                "--dataset",
                str(source),
                "--sample",
                str(sample),
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


def test_unavailable_host_observation_stops_before_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(latency, "competing_benchmark_pids", lambda: [])
    monkeypatch.setattr(
        latency, "host_sample", lambda: {"cpu_idle_percent": None, "memory_free_percent": 70}
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("Measurement began without a valid host observation")

    monkeypatch.setattr(latency.subprocess, "Popen", unexpected)
    argv = []
    for name in [
        "older",
        "newer",
        "prior-older",
        "prior-newer",
        "dataset",
        "sample",
        "official-evidence",
        "output",
    ]:
        argv.extend(["--" + name, str(tmp_path / name)])
    with pytest.raises(ValueError, match="host-resource"):
        latency.certify(argv)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "damage", ["changed-bytes", "missing", "wrong-hash", "parent", "symlink", "unknown-historical"]
)
def test_invalid_certifier_provenance_is_rejected(tmp_path, damage):
    source = tmp_path / "current.py"
    source.write_bytes(b"# frozen source\n")
    binding = latency.bind_certifier_source(tmp_path, source, source.read_bytes())
    exclusion = {"certifying_runner_sha256": binding["sha256"], "certifying_runner_source": binding}
    if damage == "changed-bytes":
        (tmp_path / binding["path"]).write_bytes(b"changed")
    elif damage == "missing":
        (tmp_path / binding["path"]).unlink()
    elif damage == "wrong-hash":
        binding["sha256"] = "0" * 64
    elif damage == "parent":
        binding["path"] = "../outside.py"
    elif damage == "symlink":
        (tmp_path / binding["path"]).unlink()
        (tmp_path / binding["path"]).symlink_to(source)
    else:
        exclusion = {"certifying_runner_sha256": "0" * 64}
    with pytest.raises((ValueError, FileNotFoundError)):
        latency.verify_certifier_source(tmp_path, exclusion)


@pytest.mark.parametrize(
    "command",
    [
        ["run_certified_webmainbench_latency.py"],
        ["apply_webmainbench_latency_correction.py"],
        ["run_certified_benchmark.py", "webmainbench-latency"],
    ],
)
def test_latency_commands_have_help_without_private_inputs(command, tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / command[0]), *command[1:], "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
