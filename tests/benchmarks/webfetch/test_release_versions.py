"""Release compatibility follows workload and scoring, not labels alone."""
from copy import deepcopy
import json

import pytest

from lexbench_webfetch.versions import (
    catalog_release, compare_releases, create_release, freeze_release, verify_release,
)


@pytest.fixture
def matching_catalog(tmp_path):
    from lexbench_webfetch.benchmark import SUITES
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
    release = json.loads((SUITES / "benchmark-release.json").read_text())
    release["scoring_source_sha256"] = SCORING_SOURCE_SHA256
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(release))
    return path


def test_catalog_binds_every_registered_suite(matching_catalog):
    from lexbench_webfetch.benchmark import registry
    release = catalog_release(matching_catalog)
    assert release["tag"] == "bench-v" + release["version"]
    entries = registry()["suites"]
    assert [row["suite"] for row in release["suites"]] == [row["id"] for row in entries]
    assert release["case_count"] == sum(row["case_count"] for row in entries)


@pytest.mark.parametrize("change", ["omitted_suite", "count", "reference", "scorer", "tag"])
def test_published_catalog_rejects_changed_population_rules_or_reference_bindings(tmp_path, change, matching_catalog):
    release = catalog_release(matching_catalog)
    if change == "omitted_suite":
        release["suites"].pop()
    elif change == "count":
        release["case_count"] += 1
    elif change == "reference":
        release["suites"][0]["dataset_sha256"] = "0" * 64
    elif change == "scorer":
        release["scoring_source_sha256"] = "0" * 64
    else:
        release["tag"] = "bench-v99.0.0"
    path = tmp_path / "release.json"
    path.write_text(json.dumps(release))
    with pytest.raises(ValueError):
        catalog_release(path)


def dataset(track="web"):
    return {"id": "sample", "cases": [{
        "id": "first", "url": "https://example.test/a", "track": track,
        "task_prompt": "Return the full article.",
        "required_facts": [{"id": "topic", "alternatives": ["complete body"]}],
        "ground_truth_main_content": "complete body",
    }]}


def release(data=None, version="1.0.0", **kwargs):
    return create_release(data or dataset(), benchmark="sample-benchmark",
                          version=version, **kwargs)


@pytest.mark.parametrize("track", ["web", "dynamic", "challenge", "risk_control", "articles", "document"])
def test_identical_measurement_remains_comparable_across_documentation_patch(track):
    before = dataset(track)
    first = release(before)
    after = deepcopy(before)
    after["name"] = "A clearer report name"
    after["cases"][0]["name"] = "A clearer task label"
    second = release(after, "1.0.1", previous=first)
    assert compare_releases(first, second)["mode"] == "direct"
    verify_release(second, after)


@pytest.mark.parametrize("change", ["add", "remove", "url", "prompt", "scope", "route"])
def test_workload_changes_require_a_major_release(change):
    before = dataset()
    before["cases"].append({**deepcopy(before["cases"][0]), "id": "second"})
    after = deepcopy(before)
    if change == "add":
        after["cases"].append({**deepcopy(after["cases"][0]), "id": "third"})
    elif change == "remove":
        after["cases"].pop()
    elif change == "url":
        after["cases"][0]["url"] += "/different"
    elif change == "prompt":
        after["cases"][0]["task_prompt"] = "Return only the heading."
    elif change == "scope":
        after["cases"][0]["task_kind"] = "file"
    else:
        after["cases"][0]["retrieval_hints"] = {"wait_until": "selector", "selector": ".body"}
    first = release(before)
    with pytest.raises(ValueError, match="major"):
        release(after, "1.1.0", previous=first, change_note="New task scope")
    second = release(after, "2.0.0", previous=first, change_note="New task scope")
    assert compare_releases(first, second)["mode"] == "separate"


@pytest.mark.parametrize("field,value", [
    ("required_facts", [{"id": "topic", "alternatives": ["complete body", "equivalent body"]}]),
    ("ground_truth_main_content", "corrected body"),
    ("body_requirements", {"regions": [{"id": "body", "text": "corrected body"}]}),
])
def test_scoring_corrections_require_minor_version_and_rescore(field, value):
    first = release()
    after = dataset()
    after["cases"][0][field] = value
    with pytest.raises(ValueError, match="minor"):
        release(after, "1.0.1", previous=first, change_note="Correct independently reviewed text")
    with pytest.raises(ValueError, match="change note"):
        release(after, "1.1.0", previous=first)
    second = release(after, "1.1.0", previous=first, change_note="Correct independently reviewed text")
    assert compare_releases(first, second)["mode"] == "rescore"


def test_scorer_changes_cannot_silently_relabel_old_scores():
    first = release(scoring_source_sha256="a" * 64)
    with pytest.raises(ValueError, match="minor"):
        release(version="1.0.1", previous=first, scoring_source_sha256="b" * 64)
    second = release(version="1.1.0", previous=first,
                     scoring_source_sha256="b" * 64, change_note="Fix serialization matching")
    assert compare_releases(first, second)["mode"] == "rescore"
    with pytest.raises(ValueError, match="scoring source"):
        verify_release(first, dataset(), scoring_source_sha256="b" * 64)


def test_same_version_cannot_name_different_contracts():
    first = release()
    after = dataset()
    after["cases"][0]["required_facts"] = []
    with pytest.raises(ValueError, match="reused"):
        compare_releases(first, release(after))


def test_different_benchmarks_and_majors_are_separate_even_when_inputs_match():
    first = release()
    other = create_release(dataset(), benchmark="another-benchmark", version="1.0.0")
    assert compare_releases(first, other)["mode"] == "separate"
    assert compare_releases(first, release(version="2.0.0"))["mode"] == "separate"


@pytest.mark.parametrize("version", ["latest", "1.0", "01.0.0", "1.0.0-dev", "1.0.-1"])
def test_mutable_or_ambiguous_version_labels_are_rejected(version):
    with pytest.raises(ValueError, match="version"):
        release(version=version)


def test_dataset_drift_and_duplicate_ids_are_rejected():
    first = release()
    after = dataset()
    after["cases"][0]["required_facts"] = []
    with pytest.raises(ValueError, match="dataset"):
        verify_release(first, after)
    after["cases"].append(deepcopy(after["cases"][0]))
    with pytest.raises(ValueError, match="unique"):
        release(after)


def test_freeze_is_exclusive_and_manifest_is_portable(tmp_path):
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(dataset()))
    target = tmp_path / "benchmark-release.json"
    freeze_release(source, target, benchmark="sample-benchmark", version="1.0.0")
    frozen = json.loads(target.read_text())
    verify_release(frozen, dataset())
    assert str(tmp_path) not in target.read_text()
    with pytest.raises(FileExistsError):
        freeze_release(source, target, benchmark="sample-benchmark", version="1.0.1")
    link = tmp_path / "link.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        freeze_release(link, tmp_path / "another.json", benchmark="sample-benchmark", version="1.0.0")


def test_public_cli_freezes_and_compares_real_files(tmp_path, capsys, monkeypatch):
    from lexbench_webfetch.cli import main
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(dataset()))
    frozen = tmp_path / "release.json"
    monkeypatch.setattr("sys.argv", ["lexbench-webfetch", "release", "--dataset", str(source),
                                    "--output", str(frozen), "--benchmark", "sample-benchmark",
                                    "--version", "1.0.0", "--scorer-revision", "a" * 40])
    main()
    assert json.loads(capsys.readouterr().out)["version"] == "1.0.0"
    monkeypatch.setattr("sys.argv", ["lexbench-webfetch", "compare-releases", str(frozen), str(frozen)])
    main()
    assert json.loads(capsys.readouterr().out)["mode"] == "direct"


def test_scorer_commit_is_a_locator_and_source_hash_owns_compatibility():
    first = release(scorer_revision="a" * 40)
    second = release(version="1.0.1", previous=first, scorer_revision="b" * 40)
    assert first["scorer_revision"] == "a" * 40
    assert compare_releases(first, second)["mode"] == "direct"
    with pytest.raises(ValueError, match="Git commit"):
        release(scorer_revision="main")


@pytest.mark.parametrize("field", ["ordered_tree_reference", "ordered_tree_min_similarity"])
@pytest.mark.parametrize("reverse", [False, True])
def test_tree_verdict_changes_require_rescoring_even_in_relaxed_report_verification(field, reverse):
    from lexbench_webfetch.evaluation import evaluate_response

    before = dataset()
    case = before["cases"][0]
    case.update(ordered_tree_reference={"tag": "p", "text": "complete body"},
                ordered_tree_min_similarity=0.5)
    after = deepcopy(before)
    if field == "ordered_tree_reference":
        after["cases"][0][field]["text"] = "world"
    else:
        case["ordered_tree_reference"]["text"] = "world"
        after["cases"][0]["ordered_tree_reference"]["text"] = "world"
        case[field] = 0.1
    if reverse:
        before, after = after, before
    answer = b'{"tag":"p","text":"complete body"}'
    assert evaluate_response(before["cases"][0], answer)["passed"] is not reverse
    assert evaluate_response(after["cases"][0], answer)["passed"] is reverse
    first = release(before)
    with pytest.raises(ValueError, match="minor"):
        release(after, "1.0.1", previous=first)
    with pytest.raises(ValueError, match="dataset"):
        verify_release(first, after, exact_dataset=False)
    corrected = release(after, "1.1.0", previous=first, change_note="Correct frozen tree rule")
    assert compare_releases(first, corrected)["mode"] == "rescore"


def test_list_name_rule_is_part_of_measurement_not_just_public_display_fields():
    from lexbench_webfetch.evaluation import evaluate_response

    before = dataset()
    before["cases"][0]["source_benchmark"] = {
        "mode": "dynamic_list_structure", "hosts": ["example.test"],
        "entry_path": r"/items/\d+", "minimum_entries": 1,
    }
    after = deepcopy(before)
    after["cases"][0]["source_benchmark"]["name_validation"] = "nonempty"
    answer = b'<a href="https://example.test/items/1">7</a>'
    assert evaluate_response(before["cases"][0], answer)["metrics"]["structure_pass"] is False
    assert evaluate_response(after["cases"][0], answer)["metrics"]["structure_pass"] is True
    first = release(before)
    with pytest.raises(ValueError, match="minor"):
        release(after, "1.0.1", previous=first)
    with pytest.raises(ValueError, match="dataset"):
        verify_release(first, after, exact_dataset=False)


@pytest.mark.parametrize("released", [False, True])
def test_version_command_distinguishes_published_and_unreleased_scorers(monkeypatch, capsys, released):
    from lexbench_webfetch import versions
    from lexbench_webfetch.benchmark import SUITES
    from lexbench_webfetch.cli import main
    catalog = json.loads((SUITES / "benchmark-release.json").read_text())
    source_hash = catalog["scoring_source_sha256"] if released else "0" * 64
    monkeypatch.setattr(versions, "SCORING_SOURCE_SHA256", source_hash)
    monkeypatch.setattr("sys.argv", ["lexbench-webfetch", "version"])
    main()
    result = json.loads(capsys.readouterr().out)
    if released:
        assert result == catalog
        assert versions.catalog_release() == catalog
    else:
        assert result["status"] == "unreleased"
        assert result["version"] is None and result["tag"] is None
        assert result["scoring_source_sha256"] == source_hash
        assert result["published_catalog"] == catalog
        with pytest.raises(ValueError, match="scoring source changed"):
            versions.catalog_release()
