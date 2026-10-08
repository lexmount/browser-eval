"""TEDS revisions preserve official history and expose only complete evidence."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.web_fetch import webmainbench_diagnostics as diagnostics
from harness.web_fetch.webmainbench_scoring import aggregate


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "source"
    (source / "references").mkdir(parents=True)
    repo = tmp_path / "scorer"
    package = repo / "webmainbench/metrics"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "base.py").write_text(
        "from dataclasses import dataclass\n"
        "@dataclass\nclass MetricResult:\n"
        "    name: str\n    score: float\n    details: dict\n"
        "    success: bool\n    error_message: str | None = None\n"
    )
    (package / "calculator.py").write_text(
        "from .base import MetricResult\n"
        "class TableMetric:\n"
        "    def calculate(self, *args, **kwargs):\n"
        "        return MetricResult('table_TEDS', 0.4, {}, True)\n"
        "class MetricCalculator:\n"
        "    def __init__(self, config):\n"
        "        self.metrics = {'table_TEDS': TableMetric()}\n"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "Fixture",
        ],
        check=True,
    )
    previous = {
        name: value
        for name, value in sys.modules.items()
        if name == "webmainbench" or name.startswith("webmainbench.")
    }
    cases, rows, summaries = [], [], {}
    names = ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS")
    for identifier in ("one", "two"):
        reference = source / "references" / (identifier + ".md")
        reference.write_text("reference")
        cases.append({"id": identifier, "meta": {}})
        for provider in ("first", "second"):
            artifact = source / (provider + "-" + identifier + ".md")
            artifact.write_text("response")
            metrics = {name: {"score": 0.5, "success": True, "details": {}} for name in names}
            metrics["overall"] = {
                "score": 0.5,
                "success": True,
                "details": {"individual_scores": dict.fromkeys(names, 0.5)},
            }
            official = copy.deepcopy(metrics)
            for value in official.values():
                value["score"] = 0.8
            rows.append(
                {
                    "provider": provider,
                    "case_id": identifier,
                    "attempt_id": provider + "__" + identifier,
                    "ok": True,
                    "artifact_path": artifact.name,
                    "output_sha256": diagnostics.digest(artifact),
                    "reference_sha256": diagnostics.digest(reference),
                    "metrics": metrics,
                    "official_metrics": official,
                }
            )
    for provider in ("first", "second"):
        selected = [row for row in rows if row["provider"] == provider]
        summaries[provider] = {
            "official_aggregate": aggregate(selected),
            "original_official_aggregate": aggregate(
                [{**row, "metrics": row["official_metrics"]} for row in selected]
            ),
            "scorer": {"metric_source_sha256": {}},
            "dimensions": {},
        }
    (source / "dataset.json").write_text(
        json.dumps({"cases": cases, "coverage_plan": {"static_html": ["first", "second"]}})
    )
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (source / "score-summaries.json").write_text(json.dumps(summaries))
    yield source, repo, rows, summaries
    for name in list(sys.modules):
        if name == "webmainbench" or name.startswith("webmainbench."):
            del sys.modules[name]
    sys.modules.update(previous)


def run_revision(monkeypatch, source, repo, output):
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "correct",
            "--source",
            str(source),
            "--scorer-repo",
            str(repo),
            "--output",
            str(output),
            "--scorer-commit",
            head,
        ],
    )
    diagnostics.correct_teds()


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_revision_retains_first_official_scores_and_aggregate(inputs, tmp_path, monkeypatch):
    source, repo, rows, summaries = inputs
    before = snapshot(source)
    output = tmp_path / "revised"
    run_revision(monkeypatch, source, repo, output)
    after = diagnostics.load_jsonl(output / "results.jsonl")
    for original, revised in zip(rows, after):
        assert revised["official_metrics"] == original["official_metrics"]
        assert revised["metrics"]["table_TEDS"]["score"] == 0.4
    revised_summaries = json.loads((output / "score-summaries.json").read_text())
    for provider, summary in summaries.items():
        assert (
            revised_summaries[provider]["original_official_aggregate"]
            == summary["original_official_aggregate"]
        )
    assert snapshot(source) == before
    audit = diagnostics.review_teds_correction(source, output)
    (output / "teds-correction-review.json").write_text(json.dumps(audit))
    diagnostics.validate_teds_revision(output)


def test_receipt_write_failure_never_exposes_partial_revision(inputs, tmp_path, monkeypatch):
    source, repo, *_ = inputs
    before = snapshot(source)
    write_text = Path.write_text

    def fail_receipt(path, *args, **kwargs):
        if path.name == "teds-correction.json":
            raise OSError("disk full")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_receipt)
    output = tmp_path / "revised"
    with pytest.raises(OSError, match="disk full"):
        run_revision(monkeypatch, source, repo, output)
    assert not output.exists()
    assert snapshot(source) == before


def test_changed_source_during_scoring_is_rejected(inputs, tmp_path, monkeypatch):
    source, repo, *_ = inputs
    factory = diagnostics.official_calculator

    def changing_scorer(*args):
        calculator = factory(*args)
        metric = calculator.metrics["table_TEDS"]
        calculate = metric.calculate

        def change_source(*args, **kwargs):
            result = calculate(*args, **kwargs)
            (source / "first-one.md").write_text("changed during scoring")
            return result

        metric.calculate = change_source
        return calculator

    monkeypatch.setattr(diagnostics, "official_calculator", changing_scorer)
    output = tmp_path / "revised"
    with pytest.raises(ValueError, match="changed"):
        run_revision(monkeypatch, source, repo, output)
    assert not output.exists()


def test_output_inside_source_is_rejected_before_writing(inputs, monkeypatch):
    source, repo, *_ = inputs
    before = snapshot(source)
    with pytest.raises(ValueError, match="outside"):
        run_revision(monkeypatch, source, repo, source / "revised")
    assert snapshot(source) == before


def test_verified_prior_revision_can_be_inherited(inputs, tmp_path, monkeypatch):
    source, repo, *_ = inputs
    prior = tmp_path / "prior"
    run_revision(monkeypatch, source, repo, prior)
    audit = diagnostics.review_teds_correction(source, prior)
    (prior / "teds-correction-review.json").write_text(json.dumps(audit))
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    output = tmp_path / "inherited"
    receipt = diagnostics.revise_teds(
        source, output, repo, prior_corrected=prior, scorer_commit=head
    )
    assert receipt["inherited_logical_results"] == 4
    assert receipt["inherited_correction_receipt_sha256"] == diagnostics.digest(
        prior / "teds-correction.json"
    )
    assert (output / "results.jsonl").read_bytes() == (prior / "results.jsonl").read_bytes()
    assert diagnostics.review_teds_correction(source, output)["status"] == "passed"


def test_first_revision_captures_the_original_summary(inputs, tmp_path, monkeypatch):
    source, repo, rows, summaries = inputs
    for row in rows:
        del row["official_metrics"]
    for summary in summaries.values():
        del summary["original_official_aggregate"]
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (source / "score-summaries.json").write_text(json.dumps(summaries))
    output = tmp_path / "first-revision"
    run_revision(monkeypatch, source, repo, output)
    revised = json.loads((output / "score-summaries.json").read_text())
    for provider, summary in summaries.items():
        assert revised[provider]["original_official_aggregate"] == summary["official_aggregate"]
    assert diagnostics.review_teds_correction(source, output)["status"] == "passed"


def test_prior_evidence_changes_during_reuse_prevent_installation(inputs, tmp_path, monkeypatch):
    source, repo, *_ = inputs
    prior = tmp_path / "prior"
    run_revision(monkeypatch, source, repo, prior)
    audit = diagnostics.review_teds_correction(source, prior)
    (prior / "teds-correction-review.json").write_text(json.dumps(audit))
    factory = diagnostics.official_calculator

    def changing_scorer(*args):
        calculator = factory(*args)
        (prior / "first-one.md").write_text("changed during reuse")
        return calculator

    monkeypatch.setattr(diagnostics, "official_calculator", changing_scorer)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    output = tmp_path / "inherited"
    with pytest.raises(ValueError, match="Inherited.*changed"):
        diagnostics.revise_teds(source, output, repo, prior_corrected=prior, scorer_commit=head)
    assert not output.exists()
