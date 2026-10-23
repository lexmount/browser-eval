"""Collection, release and participant substitution regression scenarios."""

import json
import os
from unittest.mock import patch

import pytest

from harness.web_search.core import digest
from harness.web_search.profiles import load_profile_bytes
from harness.web_search.runner import run
from harness.web_search import report
from lexbench_websearch.inputs import definition_path
from tests.web_search.test_serp import synthetic_serp_profile


def response(url=None, body=None, header=None, timeout=20):
    return (
        {"http_status": 200, "elapsed_ms": 1, "transport_exit": 0, "transport_error": ""},
        b"reference " * 20 if body is None else b'{"results":[{"url":"https://example.org/"}]}',
    )


def test_suffix_sharing_methods_have_independent_quotas(tmp_path):
    profile = synthetic_serp_profile()
    profile["cases"] = profile["cases"][:1]
    base = profile["methods"][0]
    profile["methods"] = [{**base, "id": mid} for mid in ("variant-a", "a")]
    profile["protocol"]["max_calls_per_method"] = 1
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile))
    output = tmp_path / "run"
    with (
        patch("harness.web_search.runner.load_runtime_env"),
        patch.dict(os.environ, {base["key_env"]: "synthetic-key"}),
        patch("harness.web_search.runner.http_call", side_effect=response),
    ):
        run(source, output, certified=True)
    from harness.web_search.runner import physical_call_counts

    assert physical_call_counts(profile, output) == {"variant-a": 1, "a": 1}
    archive = output / "resume-1/calls"
    archive.mkdir(parents=True)
    name = "fixture-0-variant-a-r0-attempt-1.json"
    (output / "calls" / name).rename(archive / name)
    assert physical_call_counts(profile, output) == {"variant-a": 1, "a": 1}
    (output / "calls" / name).write_text("{}")
    assert physical_call_counts(profile, output) == {"variant-a": 2, "a": 1}


@pytest.mark.parametrize("tail", [b"", b'{"id":', b'{"id":"\xe4', b'{"id":\n'])
@pytest.mark.parametrize("selection", [None, "primary", "supplement"])
def test_execution_revision_resolves_composed_profiles_and_current_cohorts(tmp_path, selection, tail):
    profile = synthetic_serp_profile()
    profile["methods"] = profile["methods"][:1]
    dataset = definition_path("datasets/scenarios-v1.json")
    profile["cases"] = {
        "field": "cases",
        "benchmark_datasets": [{"id": "scenarios-v1", "sha256": digest(dataset.read_bytes())}],
    }
    compact = tmp_path / "compact.json"
    source = compact if selection is None else tmp_path / "current.json"

    def save():
        compact.write_text(json.dumps(profile))
        if selection:
            binding = {"path": compact.name, "sha256": digest(compact.read_bytes())}
            source.write_text(
                json.dumps(
                    {
                        "schema": "web-search-current/1",
                        "title": profile["title"],
                        "profile": binding,
                        "methods": [profile["methods"][0]["id"]],
                        "supplementary_profiles": [{**binding, "id": "supplement"}],
                        "publication_target": "target.json",
                    }
                )
            )

    save()
    cohort = selection or "primary"
    old_bytes = load_profile_bytes(source, cohort=cohort)
    output = tmp_path / "run"
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        if len(args) < 2 or args[1] is None:
            return response(*args, **kwargs)
        if calls:
            raise RuntimeError("Interrupted")
        calls += 1
        return response(*args, **kwargs)

    key = profile["methods"][0]["key_env"]
    with (
        patch("harness.web_search.runner.load_runtime_env"),
        patch.dict(os.environ, {key: "synthetic-key"}),
    ):
        with patch("harness.web_search.runner.http_call", side_effect=interrupted):
            with pytest.raises(RuntimeError, match="Interrupted"):
                run(source, output, certified=True, cohort=cohort)
        records = output / "results.jsonl"
        interrupted_bytes = records.read_bytes() + tail
        records.write_bytes(interrupted_bytes)
        profile["methods"][0]["endpoint"] += "/new"
        save()
        new_bytes = load_profile_bytes(source, cohort=cohort)
        proof = tmp_path / "proof.json"
        proof.write_text('{"accepted":true}')
        revision = tmp_path / "revision.json"
        revision.write_text(
            json.dumps(
                {
                    "schema": "web-search-execution-revision/1",
                    "root_cause": "Changed native endpoint",
                    "superseded_methods": [profile["methods"][0]["id"]],
                    "previous_profile_sha256": digest(old_bytes),
                    "profile_sha256": digest(new_bytes),
                    "evidence": [{"path": proof.name, "sha256": digest(proof.read_bytes())}],
                }
            )
        )
        if tail.endswith(b"\n"):
            with patch("harness.web_search.runner.http_call", side_effect=response):
                with pytest.raises(json.JSONDecodeError):
                    run(source, output, resume=True, execution_revision=revision, certified=True, cohort=cohort)
            assert records.read_bytes() == interrupted_bytes
            assert not (output / "resume-1").exists()
            return
        with patch("harness.web_search.runner.http_call", side_effect=response):
            run(
                source,
                output,
                resume=True,
                execution_revision=revision,
                certified=True,
                cohort=cohort,
            )
    assert (output / "profile.json").read_bytes() == new_bytes
    assert (output / "resume-1/previous-profile.json").read_bytes() == old_bytes
    assert (output / "resume-1/previous-results.jsonl").read_bytes() == interrupted_bytes
    frozen = json.loads(new_bytes)
    assert len((output / "results.jsonl").read_text().splitlines()) == len(frozen["cases"])
    if selection:
        assert frozen["current_selection"]["cohort"] == cohort


@pytest.mark.parametrize("zh", [False, True])
def test_catalog_prose_follows_selected_participants(zh):
    profile = {"methods": [{"id": "alpha", "label": "Alpha"}]}
    content = {
        "catalog_pricing": {
            "alpha": {
                "amount": 2,
                "currency": "USD",
                "unit": "1000-successful-searches",
                "source": "https://alpha.test/pricing",
                "checked_at": "2026-10-23",
                "terms": {"en": "Alpha terms", "zh": "Alpha terms"},
            }
        }
    }
    html = report.catalog_price_panel(profile, content, zh)
    assert "Alpha terms" in html and "Alpha" in html
    assert all(name not in html for name in ("Browserbase", "Bright Data", "Serper"))


def test_combined_release_export_keeps_the_explicit_publication_plan(tmp_path, monkeypatch):
    output, visual, plan, delivery = [
        tmp_path / name for name in ("report", "visual.json", "publication-plan.json", "delivery")
    ]
    output.mkdir()
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps({"receipt": {"certified": True}}))
    visual.write_text(json.dumps({"manifest_sha256": digest(manifest.read_bytes())}))
    monkeypatch.setattr(report, "check", lambda _: None)
    with (
        patch("harness.web_search.publication.verify_plan") as verify_plan,
        patch("harness.web_search.visual.verify"),
        patch("harness.web_search.delivery.export") as export,
        patch("harness.web_search.delivery.verify") as verify,
    ):
        with pytest.raises(ValueError, match="explicit publication plan"):
            report.main(
                [
                    "--output",
                    str(output),
                    "--release-check",
                    str(visual),
                    "--export-delivery",
                    str(delivery),
                ]
            )
        export.assert_not_called()
        report.main(
            [
                "--output",
                str(output),
                "--release-check",
                str(visual),
                "--publication-plan",
                str(plan),
                "--export-delivery",
                str(delivery),
            ]
        )
    verify_plan.assert_called_once_with(output, plan.parent)
    export.assert_called_once_with(output, delivery)
    verify.assert_called_once_with(output, delivery)
