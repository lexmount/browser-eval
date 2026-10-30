"""Certified cohort scoring preserves evidence and installs complete results only."""

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

from harness.record_io import load_jsonl
from harness.web_fetch import wcxb_scoring as scoring
from harness.web_fetch.safety import evidence_tree_identity
from tests.wcxb_fixture import create_cohort, sha, write_json, write_jsonl


@pytest.fixture
def cohort(tmp_path):
    return create_cohort(tmp_path)


def outputs(options):
    root = options["evidence_root"]
    return [root / "wcxb-scores.jsonl", root / "wcxb-score-summary.json"]


def assert_failure_preserves_evidence(options, match):
    before = evidence_tree_identity(options["evidence_root"])
    with pytest.raises((ValueError, FileNotFoundError), match=match):
        scoring.score(**options)
    assert evidence_tree_identity(options["evidence_root"]) == before
    assert not any(path.exists() for path in outputs(options))


def rebind_results(options, provider, rows):
    root = options["evidence_root"]
    result = root / provider / "results.jsonl"
    write_jsonl(result, rows)
    certificate = json.loads((root / "certification.json").read_bytes())
    next(row for row in certificate["providers"] if row["provider"] == provider)[
        "results_sha256"
    ] = sha(result.read_bytes())
    write_json(root / "certification.json", certificate)


def test_custom_participant_matrix_preserves_scores_retries_and_populations(cohort):
    root = cohort["evidence_root"]
    before = evidence_tree_identity(root)
    summary = scoring.score(**cohort)
    assert list(summary["providers"]) == ["alpha", "beta", "gamma"]
    rows = load_jsonl(root / "wcxb-scores.jsonl")
    assert len(rows) == 6
    assert all(row["f1"] == 1 for row in rows if row["provider"] != "beta")
    beta = summary["providers"]["beta"]
    assert beta["cases"] == 2 and beta["delivered"] == 1
    assert beta["f1"] == 0.5
    assert beta["physical_call_count"] == 3
    assert beta["mean_latency_ms"] == 2 and beta["p95_latency_ms"] == 3
    assert beta["success_latency_count"] == 1 and beta["success_mean_latency_ms"] == 1
    failure = next(row for row in rows if row["provider"] == "beta" and not row["delivered"])
    assert failure["diagnosis"] == "request_error"
    assert failure["physical_attempt_count"] == 2
    assert failure["total_elapsed_ms"] == 3
    assert len(failure["retry_failures"]) == 1
    assert (root / failure["retry_failures"][0]["artifact_path"]).read_bytes() == b""
    assert all(sha((root / name).read_bytes()) == value for name, value in before.items())
    assert json.loads((root / "wcxb-score-summary.json").read_bytes()) == summary
    with pytest.raises(ValueError, match="overwrite"):
        scoring.score(**cohort)


def test_paired_timing_joins_binary_identity_with_arbitrary_browser_names(tmp_path):
    options = create_cohort(tmp_path, paired=True)
    summary = scoring.score(**options)
    assert summary["providers"]["alpha"]["mean_latency_ms"] == 11
    assert summary["providers"]["gamma"]["mean_latency_ms"] == 13
    assert summary["providers"]["beta"]["mean_latency_ms"] == 2
    rows = load_jsonl(options["evidence_root"] / "wcxb-scores.jsonl")
    assert all(row["prior_total_elapsed_ms"] == 1 for row in rows if row["provider"] != "beta")
    assert all(
        row["latency_source"] == "paired_final_round" for row in rows if row["provider"] != "beta"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "late_artifact",
        "final_physical",
        "prior_physical",
        "physical_escape",
        "logical_escape",
        "linked_artifact",
        "duplicate_call",
        "call_order",
        "input_hash",
        "reference_hash",
        "run_id",
        "final_outcome",
        "nan_latency",
        "execution",
        "certificate",
        "profile_matrix",
        "profile_source",
    ],
)
def test_invalid_cohort_is_rejected_without_partial_outputs(cohort, tmp_path, defect):
    root = cohort["evidence_root"]
    provider = "gamma" if defect == "late_artifact" else "beta"
    run = root / provider
    logical = load_jsonl(run / "results.jsonl")
    physical = load_jsonl(run / "physical-attempts.jsonl")
    if defect == "late_artifact":
        (run / logical[-1]["artifact_path"]).write_text("changed response")
    elif defect in ("final_physical", "prior_physical"):
        (
            run / physical[1 if defect == "final_physical" else 0]["physical_artifact_path"]
        ).write_text("changed response")
    elif defect == "physical_escape":
        physical[0]["physical_artifact_path"] = "../../outside.txt"
        write_jsonl(run / "physical-attempts.jsonl", physical)
    elif defect == "logical_escape":
        logical[0]["artifact_path"] = "../../outside.txt"
        rebind_results(cohort, provider, logical)
    elif defect == "linked_artifact":
        path = run / logical[-1]["artifact_path"]
        target = tmp_path / "linked-response.txt"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    elif defect == "duplicate_call":
        write_jsonl(run / "physical-attempts.jsonl", physical + [physical[0]])
    elif defect == "call_order":
        physical[0]["physical_attempt"] = 2
        write_jsonl(run / "physical-attempts.jsonl", physical)
    elif defect in ("input_hash", "reference_hash", "run_id", "nan_latency"):
        key = {
            "input_hash": "input_html_sha256",
            "reference_hash": "reference_sha256",
            "run_id": "run_id",
            "nan_latency": "elapsed_ms",
        }[defect]
        physical[0][key] = float("nan") if defect == "nan_latency" else "changed"
        write_jsonl(run / "physical-attempts.jsonl", physical)
    elif defect == "final_outcome":
        logical[0]["ok"] = True
        rebind_results(cohort, provider, logical)
    elif defect == "execution":
        document = json.loads((run / "execution.json").read_bytes())
        document["implementation_identity"] = {"binary_sha256": "changed"}
        write_json(run / "execution.json", document)
    elif defect == "certificate":
        document = json.loads((root / "certification.json").read_bytes())
        document["complete"] = False
        write_json(root / "certification.json", document)
    else:
        document = json.loads(cohort["profile_path"].read_bytes())
        document["providers" if defect == "profile_matrix" else "source_dataset_sha256"] = (
            ["alpha", "beta"] if defect == "profile_matrix" else "changed"
        )
        write_json(cohort["profile_path"], document)
    if defect == "linked_artifact":
        with pytest.raises(ValueError, match="[Ss]ymlink|linked"):
            scoring.score(**cohort)
        assert not any(path.exists() for path in outputs(cohort))
    else:
        assert_failure_preserves_evidence(cohort, "WCXB|Paths must")


@pytest.mark.parametrize("defect", ["missing", "incomplete", "wrong_output", "ambiguous_binary"])
def test_paired_timing_rejects_unbound_or_partial_corrections(tmp_path, defect):
    options = create_cohort(tmp_path, paired=True)
    root = options["evidence_root"]
    correction = root / "latency-correction"
    if defect == "missing":
        (correction / "calls.jsonl").unlink()
    elif defect == "incomplete":
        calls = load_jsonl(correction / "calls.jsonl")
        write_jsonl(correction / "calls.jsonl", calls[:-1])
    elif defect == "wrong_output":
        calls = load_jsonl(correction / "calls.jsonl")
        for call in calls:
            call["output_sha256"] = "a" * 64
        write_jsonl(correction / "calls.jsonl", calls)
        receipt = json.loads((correction / "receipt.json").read_bytes())
        receipt["calls_sha256"] = sha((correction / "calls.jsonl").read_bytes())
        write_json(correction / "receipt.json", receipt)
        profile = json.loads(options["profile_path"].read_bytes())
        profile["latency_correction_receipt_sha256"] = sha(
            (correction / "receipt.json").read_bytes()
        )
        write_json(options["profile_path"], profile)
        certificate = json.loads((correction / "certification.json").read_bytes())
        certificate.update(
            calls_sha256=receipt["calls_sha256"],
            receipt_sha256=profile["latency_correction_receipt_sha256"],
        )
        write_json(correction / "certification.json", certificate)
    else:
        certificate = json.loads((root / "certification.json").read_bytes())
        certificate["providers"][1]["identity"] = certificate["providers"][0]["identity"]
        write_json(root / "certification.json", certificate)
    assert_failure_preserves_evidence(options, "WCXB")


def test_install_failure_rolls_back_new_outputs_and_keeps_existing_artifacts(cohort, monkeypatch):
    root = cohort["evidence_root"]
    retained = root / "physical-artifacts/beta/case-000-p01.txt"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"")
    before = evidence_tree_identity(root)
    original = scoring.atomic_create

    def fail_summary(path, raw):
        if path.name == "wcxb-score-summary.json":
            raise OSError("simulated storage failure")
        original(path, raw)

    monkeypatch.setattr(scoring, "atomic_create", fail_summary)
    with pytest.raises(OSError, match="storage failure"):
        scoring.score(**cohort)
    assert evidence_tree_identity(root) == before
    assert retained.exists()
    monkeypatch.setattr(scoring, "atomic_create", original)
    assert scoring.score(**cohort)["case_count"] == 2


def test_output_created_during_install_is_preserved(cohort, monkeypatch):
    original = scoring.atomic_create

    def competing_output(path, raw):
        if path.name == "wcxb-scores.jsonl":
            path.write_bytes(b"independently created output")
        original(path, raw)

    monkeypatch.setattr(scoring, "atomic_create", competing_output)
    with pytest.raises(FileExistsError):
        scoring.score(**cohort)
    assert outputs(cohort)[0].read_bytes() == b"independently created output"
    assert not outputs(cohort)[1].exists()
    assert not list((cohort["evidence_root"] / "physical-artifacts").rglob("*.txt"))


@pytest.mark.parametrize("changed", ["profile", "source", "response"])
def test_inputs_changed_during_scoring_reject_all_outputs(cohort, monkeypatch, changed):
    # Fault injection targets the evidence guard, independently of code admission.
    monkeypatch.setattr(scoring, 'metric_implementation_binding', lambda *args, **kwargs: '1' * 64)
    original = scoring.word_f1
    mutated = False

    def evaluate(*values):
        nonlocal mutated
        result = original(*values)
        if not mutated:
            target = {
                "profile": cohort["profile_path"],
                "source": cohort["source"] / "test/ground-truth/case-000.json",
                "response": cohort["evidence_root"] / "alpha/artifacts/case-000.md",
            }[changed]
            target.write_bytes(target.read_bytes() + b" ")
            mutated = True
        return result

    monkeypatch.setattr(scoring, "word_f1", evaluate)
    with pytest.raises(ValueError, match="changed during scoring"):
        scoring.score(**cohort)
    assert not any(path.exists() for path in outputs(cohort))
    assert not list((cohort["evidence_root"] / "physical-artifacts").rglob("*.txt"))


def test_dataset_python_is_never_executed_even_with_stale_bytecode(cohort, tmp_path):
    module = cohort["source"] / "evaluate.py"
    sentinel = tmp_path / "executed"
    module.write_text('raise RuntimeError("old external code executed")\n')
    stamp = module.stat()
    py_compile.compile(str(module), doraise=True)
    module.write_text(f'from pathlib import Path; Path({str(sentinel)!r}).write_text("executed")\n')
    os.utime(module, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    summary = scoring.score(**cohort)
    assert summary['official_evaluator_sha256'] == scoring.UPSTREAM_EVALUATOR_SHA256
    assert not sentinel.exists()


def test_command_uses_explicit_profile_outside_repository(cohort, tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/score_wcxb_results.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(cohort["source"]),
            "--sample-manifest",
            str(cohort["sample_manifest"]),
            "--evidence-root",
            str(cohort["evidence_root"]),
            "--profile",
            str(cohort["profile_path"]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert outputs(cohort)[1].exists()


@pytest.mark.parametrize("metric", [float('nan'), float('inf'), 1.1, True])
def test_invalid_evaluator_metrics_never_produce_score_files(cohort, metric, monkeypatch):
    # Exercise numeric validation separately from implementation admission.
    monkeypatch.setattr(scoring, 'metric_implementation_binding', lambda *args, **kwargs: '1' * 64)
    monkeypatch.setattr(scoring, 'word_f1', lambda *args: (metric, 1., 1.))
    assert_failure_preserves_evidence(cohort, "invalid metrics")


def test_execution_budget_must_match_profile(cohort):
    path = cohort["evidence_root"] / "gamma/execution.json"
    execution = json.loads(path.read_bytes())
    execution["timeout_seconds"] = 900
    write_json(path, execution)
    assert_failure_preserves_evidence(cohort, "execution differs")


def test_retry_count_cannot_exceed_declared_budget(cohort):
    profile = json.loads(cohort["profile_path"].read_bytes())
    profile["max_failure_retries"] = 0
    write_json(cohort["profile_path"], profile)
    for provider in profile["providers"]:
        path = cohort["evidence_root"] / provider / "execution.json"
        execution = json.loads(path.read_bytes())
        execution["max_failure_retries"] = 0
        write_json(path, execution)
    assert_failure_preserves_evidence(cohort, "retry or latency binding")
