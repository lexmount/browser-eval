import json
from pathlib import Path

import pytest

from lexbench_websearch import inputs
from lexbench_websearch.relevance import query_metrics, summary
from lexbench_websearch.contracts import validate_cases


def test_every_dataset_has_unique_tasks_and_exact_attribution_coverage():
    attribution = json.loads(inputs.definition_path("sources.json").read_bytes())
    sources = attribution["sources"]
    attributed = [cid for source in sources for cid in source["case_ids"]]
    tasks = []
    for identity in ("scenarios-v1", "factual-v1"):
        binding = inputs.registry()["datasets"][identity]
        data = inputs.load_dataset(identity)
        assert len(data["cases"]) == binding["count"]
        assert all(
            c.get("origin")
            and attribution["task_derivations"][c["id"]]
            and c.get("provenance_kind")
            for c in data["cases"]
        )
        tasks.extend(c["id"] for c in data["cases"])
    assert len(tasks) == len(set(tasks)) == len(attributed) == len(set(attributed))
    assert set(tasks) == set(attributed)


def test_missing_or_changed_dataset_bytes_fail_before_use(tmp_path, monkeypatch):
    from harness.benchmarks import data
    definitions = inputs.definition_path("registry.json").parent
    (tmp_path / "definitions").mkdir()
    (tmp_path / "definitions/registry.json").write_bytes(
        (definitions / "registry.json").read_bytes()
    )
    (tmp_path / "definitions/datasets").mkdir()
    for name in (definitions / "datasets").glob("*.json"):
        (tmp_path / "definitions/datasets" / name.name).write_bytes(name.read_bytes() + b" ")
    monkeypatch.setattr(data, "snapshot", lambda track, revision=None: tmp_path)
    with pytest.raises(ValueError, match="hash or size mismatch"):
        inputs.load_dataset("scenarios-v1")


def test_profile_projection_requires_exact_dataset_and_field_binding():
    binding = inputs.registry()["datasets"]["scenarios-v1"]
    reference = {
        "benchmark_dataset": "scenarios-v1",
        "sha256": binding["sha256"],
        "field": "cases",
    }
    standalone = {"cases": [{"id": "local"}]}
    assert inputs.resolve_profile(standalone) == standalone
    assert (
        len(inputs.resolve_profile({"cases": reference})["cases"]) == binding["count"]
    )
    for change in ({"sha256": "bad"}, {"field": "reference_checks"}):
        with pytest.raises(ValueError, match="binding mismatch"):
            inputs.resolve_profile({"cases": dict(reference, **change)})
    for path in ("../private", "/private"):
        with pytest.raises(ValueError):
            inputs.definition_path(path)


def test_dataset_validation_rejects_duplicate_ids_and_missing_reference_checks():
    with pytest.raises(ValueError):
        validate_cases([{"id": "q"}, {"id": "q"}])
    case = {"id": "q", "anchors": ["https://example.org/"]}
    with pytest.raises(ValueError, match="publisher assertions"):
        validate_cases([case], reference_scope="identity")
    validate_cases(
        [case],
        reference_scope="identity",
        reference_checks={
            "https://example.org/": {
                "url": "https://example.org/",
                "contains": ["Example"],
            }
        },
    )


def test_graded_metrics_cover_missing_duplicate_and_zero_gain_results():
    metrics = query_metrics(
        ["bad", "direct", "support", "direct"], {"bad": 0, "direct": 3, "support": 2}, 5
    )
    assert metrics["ndcg"] == pytest.approx(0.665315246)
    assert metrics["precision"] == 0.4
    assert metrics["success"] == 1
    assert query_metrics([], {}, 5)["ndcg"] == 0
    assert query_metrics(["zero"], {"zero": 0}, 5)["ndcg"] == 0
    for invalid in (-1, 4, True, 2.5):
        with pytest.raises(ValueError, match="grades"):
            query_metrics(["a"], {"a": invalid})
    with pytest.raises(ValueError, match="Cutoff"):
        query_metrics([], {}, 0)
    rows = [
        {"method": "a", "ndcg": value, "precision": value, "success": value}
        for value in (0.0, 1.0)
    ]
    assert summary(rows, ["a"])["a"] == {
        "n": 2,
        "ndcg": 0.5,
        "precision": 0.5,
        "success": 0.5,
    }


def test_public_cli_exports_installed_definitions_and_scores(tmp_path):
    import subprocess
    import sys

    call = lambda *args: subprocess.run(
        [sys.executable, "-m", "lexbench_websearch.cli", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    result = call("export", "scenarios-v1")
    assert result.returncode == 0 and len(json.loads(result.stdout)["cases"]) == 149
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps({"ranking": ["a"], "grades": {"a": 3}}))
    result = call("score", str(packet))
    assert result.returncode == 0 and json.loads(result.stdout)["precision"] == 0.2
    packet.write_text(json.dumps({"ranking": ["a"], "grades": {"a": 4}}))
    assert call("score", str(packet)).returncode != 0


def test_current_scenario_version_contains_qualified_references_and_aliases():
    original = inputs.load_dataset("scenarios-v1")
    current = inputs.load_dataset(inputs.registry()["current"]["scenarios"])
    old = {c["id"]: c for c in original["cases"]}
    assert [c["id"] for c in current["cases"]] == list(old)
    assert (
        sum(
            bool(c.get("anchors")) and not old[c["id"]].get("anchors")
            for c in current["cases"]
        )
        == 30
    )
    assert sum(bool(c.get("identity_aliases")) for c in current["cases"]) == 4
    validate_cases(
        current["cases"],
        reference_checks=current["reference_checks"],
        reference_scope="identity",
    )
    with pytest.raises(FileNotFoundError):
        inputs.definition_path("missing.json")


def test_distribution_contains_no_report_instances_or_approval_lists():
    for path in inputs.definition_path(".").rglob("*.json"):
        data = json.loads(path.read_bytes())
        assert "approved_sha256" not in data
        assert "publication_target" not in data
        assert "materialized_profiles" not in data
    assert not inputs.definition_path(".").joinpath("assessment-revisions.json").exists()


def test_benchmark_runtime_avoids_execution_tracks_and_provider_clients():
    import ast

    forbidden = {
        "harness.web_search",
        "harness.web_fetch",
        "harness.browser_use",
        "harness.trajectory_replay",
        "requests",
        "httpx",
        "aiohttp",
        "playwright",
        "socket",
        "subprocess",
    }
    for path in Path(inputs.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules = [node.module or ""]
            else:
                continue
            assert not any(
                any(module == owner or module.startswith(owner + ".") for owner in forbidden)
                or module in {"urllib.request", "http.client"}
                for module in modules
            ), path.name


def test_current_cli_uses_suite_names_and_preserves_frozen_snapshots(tmp_path):
    import subprocess
    import sys

    def call(*args):
        return subprocess.run(
            [sys.executable, "-m", "lexbench_websearch.cli", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

    listed = call("list")
    assert listed.returncode == 0
    assert json.loads(listed.stdout) == {
        "datasets": {"scenarios": {"cases": 149}, "factual": {"cases": 831}}
    }
    version = call("version")
    assert version.returncode == 0
    assert set(json.loads(version.stdout)["suites"]) == {"scenarios", "factual"}
    assert "scenarios-v" not in version.stdout
    for family, identity in inputs.registry()["current"].items():
        exported = call("export", family)
        assert exported.returncode == 0, exported.stderr
        assert json.loads(exported.stdout) == inputs.load_dataset(identity)
        checked = call("validate", family)
        assert checked.returncode == 0, checked.stderr
        assert json.loads(checked.stdout)["dataset"] == family
    missing = call("export", "missing-suite")
    assert missing.returncode != 0
    assert "scenarios-v" not in missing.stderr
    assert "scenarios-v" not in call("export", "--help").stdout
    historical = call("export", "scenarios-v1")
    assert historical.returncode == 0
    assert json.loads(historical.stdout) == inputs.load_dataset("scenarios-v1")
