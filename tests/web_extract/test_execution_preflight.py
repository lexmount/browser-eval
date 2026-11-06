"""The public workflow checks a bounded sample before expensive full collection."""

from unittest.mock import patch

import pytest

from harness.web_extract.certification import create_plan, execution_preflight, run
from harness.web_extract.example import create
from harness.web_extract.pipeline import load_run, read_json
from tests.web_extract.snapshot_inputs import bind_inputs, serve_input
from tests.web_extract.test_execution_boundaries import accepted, collection_context


def test_preflight_is_separate_and_full_collection_requires_its_bound_assessment(tmp_path):
    home = create(tmp_path / "example")
    candidate, final = tmp_path / "candidate.json", tmp_path / "final.json"
    assessment = tmp_path / "execution-assessment.json"
    diagnostic, measured = tmp_path / "sample", tmp_path / "measured"
    with (
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.snapshots.validate_dataset"),
    ):
        snapshot = bind_inputs(home, tmp_path / "snapshots.json")
        create_plan(
            home,
            ["cloudflare-json"],
            candidate,
            snapshots=snapshot,
            require_execution_assessment=True,
        )
    with (
        collection_context(),
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.pipeline.qualify_fixture", side_effect=serve_input),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
    ):
        with pytest.raises(ValueError, match="execution preflight"):
            run(home, candidate, "cloudflare-json", measured)
        call.assert_not_called()
        result = execution_preflight(
            home,
            candidate,
            "cloudflare-json",
            diagnostic,
            assessment,
            sample_size=2,
            wall_time_budget_seconds=600,
        )
        assert result["diagnostic"] is True and call.call_count == 2
        assert not (diagnostic / "certificate.json").exists()
        contract, rows = load_run(diagnostic)
        assert contract["kind"] == "execution_preflight" and len(rows) == 2
        original_receipt = (diagnostic / "execution-preflight.json").read_bytes()
        execution_preflight(
            home,
            candidate,
            "cloudflare-json",
            diagnostic,
            assessment,
            sample_size=2,
            wall_time_budget_seconds=600,
        )
        assert call.call_count == 2
        assert (diagnostic / "execution-preflight.json").read_bytes() == original_receipt
        create_plan(
            home,
            ["cloudflare-json"],
            final,
            snapshots=snapshot,
            require_execution_assessment=True,
            execution_assessments=[assessment],
        )
        run(home, final, "cloudflare-json", measured)
        assert call.call_count == 5
        assert read_json(measured / "certificate.json")["case_count"] == 3
        (diagnostic / "execution-preflight.json").write_text(
            "changed readiness evidence", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="evidence changed"):
            run(home, final, "cloudflare-json", measured)
        assert call.call_count == 5


def test_preflight_rejects_bad_sample_size_before_dispatch(tmp_path):
    with patch("harness.web_extract.pipeline.provider_call") as call:
        with pytest.raises(ValueError, match="sample_size"):
            execution_preflight(
                tmp_path,
                tmp_path / "profile.json",
                "cloudflare-json",
                tmp_path / "sample",
                tmp_path / "assessment.json",
                sample_size=True,
            )
        call.assert_not_called()


def test_preflight_resume_retains_time_spent_before_interruption(tmp_path):
    home = create(tmp_path / "example")
    candidate = tmp_path / "candidate.json"
    diagnostic, assessment = tmp_path / "sample", tmp_path / "assessment.json"
    with (
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.snapshots.validate_dataset"),
    ):
        snapshot = bind_inputs(home, tmp_path / "snapshots.json")
        create_plan(home, ["cloudflare-json"], candidate, snapshots=snapshot)
    with (
        collection_context(),
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.certification.time.time", return_value=100),
        patch("harness.web_extract.certification.probe", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        execution_preflight(
            home, candidate, "cloudflare-json", diagnostic, assessment, sample_size=2
        )
    with (
        collection_context(),
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.certification.time.time", return_value=200),
        patch("harness.web_extract.pipeline.qualify_fixture", side_effect=serve_input),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted),
    ):
        execution_preflight(
            home, candidate, "cloudflare-json", diagnostic, assessment, sample_size=2
        )
    assert read_json(diagnostic / "execution-preflight.json")["wall_seconds"] >= 100
