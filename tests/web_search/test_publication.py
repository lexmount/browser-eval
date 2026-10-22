"""A frozen publication plan is checked before authentication or network calls."""
import json

import pytest

from harness.web_search import publication, profiles
from harness.web_search.core import digest


def publication_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(publication, "ROOT", tmp_path)
    monkeypatch.setattr(publication, "current_selection", lambda: {"publication_target": "target.json"})
    report = tmp_path / "report"
    work = tmp_path / "release"
    report.mkdir()
    (work / "delivery").mkdir(parents=True)
    manifest = {"profile": {"cases": [{}]}, "rows": [{"attempts": [{}]}]}
    (report / "manifest.json").write_text(json.dumps(manifest))
    manifest_hash = digest((report / "manifest.json").read_bytes())
    files = {
        "report_manifest": (report / "manifest.json").read_bytes(),
        "visual_acceptance": json.dumps({"manifest_sha256": manifest_hash}).encode(),
        "delivery_index": b"{}", "relevance": b"{}", "independent_review": b"{}", "content": b"{}"
    }
    bindings = {}
    for name, raw in files.items():
        source = work / (name + ".json")
        source.write_bytes(raw)
        bindings[name] = {"path": str(source.relative_to(tmp_path)), "sha256": digest(raw)}
    manifest.update(relevance_sha256=bindings["relevance"]["sha256"], independent_review_sha256=bindings["independent_review"]["sha256"], content_sha256=bindings["content"]["sha256"])
    (report / "manifest.json").write_text(json.dumps(manifest))
    manifest_hash = digest((report / "manifest.json").read_bytes())
    (work / "report_manifest.json").write_bytes((report / "manifest.json").read_bytes())
    bindings["report_manifest"]["sha256"] = manifest_hash
    (work / "visual_acceptance.json").write_text(json.dumps({"manifest_sha256": manifest_hash}))
    bindings["visual_acceptance"]["sha256"] = digest((work / "visual_acceptance.json").read_bytes())
    (work / "delivery/delivery-index.json").write_bytes(b"{}")
    (work / "report.zip").write_bytes(b"frozen archive")
    target = {"bindings": bindings, "cases": 1, "logical_results": 1, "physical_calls": 1, "archive_sha256": digest(b"frozen archive")}
    (tmp_path / "target.json").write_text(json.dumps(target))
    plan = {"expected_target": {}, "target": target, "archive_sha256": target["archive_sha256"]}
    (work / "publication-plan.json").write_text(json.dumps(plan))
    # The identity validation itself is covered by CurrentProfileScenarios.
    monkeypatch.setattr(profiles, "publication_authority", lambda *args, **kwargs: target)
    return report, work


def test_publication_plan_uses_configured_data_home(tmp_path, monkeypatch):
    report, work = publication_files(tmp_path, monkeypatch)
    assert publication.verify_plan(report, work)["target"]["cases"] == 1


@pytest.mark.parametrize("relative", ["report.zip", "visual_acceptance.json", "relevance.json", "independent_review.json", "content.json", "delivery/delivery-index.json"])
def test_changed_release_inputs_stop_before_authentication(tmp_path, monkeypatch, relative):
    report, work = publication_files(tmp_path, monkeypatch)
    (work / relative).write_bytes(b"changed")
    monkeypatch.setattr(publication, "load_token", lambda **kwargs: pytest.fail("Authenticated before checking the frozen package"))
    with pytest.raises((ValueError, json.JSONDecodeError)):
        publication.publish(report, work)
