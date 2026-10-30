"""Release compatibility follows request and scoring behavior, not filenames."""

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from lexbench_websearch.inputs import load_dataset, registry
from lexbench_websearch.versions import (
    catalog_release,
    compare_releases,
    create_release,
    freeze_release,
    verify_release,
)

REVISION = "a" * 40


def dataset():
    return {
        "cases": [
            {
                "id": "question",
                "query": "Find the official manual",
                "anchors": ["https://example.org/manual"],
            }
        ]
    }


def release(data=None, version="1.0.0", **options):
    return create_release(
        data or dataset(), version=version, scorer_revision=REVISION, **options
    )


def test_installed_catalog_is_complete_and_verifiable():
    edition = catalog_release(require_current_scorer=False)
    assert edition["tag"] == "bench-v" + edition["version"]
    assert set(edition["datasets"]) == set(registry()["datasets"])
    for name, bound in edition["datasets"].items():
        verify_release(bound, load_dataset(name), verify_scorer=False)


@pytest.mark.parametrize(
    "change",
    [
        {"query": "A different question"},
        {"domains": ["example.org"]},
        {"output_scope": "full_text"},
        {"intent": "A different task"},
    ],
)
def test_changed_requests_need_major_and_new_calls(change):
    data = dataset()
    data["cases"][0].update(change)
    original = release()
    with pytest.raises(ValueError, match="major"):
        release(data, "1.1.0", previous=original)
    changed = release(data, "2.0.0", previous=original)
    assert compare_releases(original, changed)["mode"] == "separate"


def test_population_changes_need_major():
    data = dataset()
    data["cases"].append({"id": "another", "query": "Another question"})
    with pytest.raises(ValueError, match="major"):
        release(data, "1.1.0", previous=release())


def test_reference_correction_requires_minor_and_changes_real_verdict():
    from lexbench_websearch.scoring import task_acceptance

    old = dataset()
    new = deepcopy(old)
    new["cases"][0]["anchors"] = ["https://example.org/corrected"]
    # A reference correction changes which retrieved page receives credit.
    for url in ("https://example.org/manual", "https://example.org/corrected"):
        rows = [{"url": url, "title": "Manual", "snippet": "Manual"}]
        assert task_acceptance(
            old["cases"][0], {"results": rows}, "source"
        ) != task_acceptance(new["cases"][0], {"results": rows}, "source")
    original = release(old)
    with pytest.raises(ValueError, match="minor"):
        release(new, "1.0.1", previous=original)
    with pytest.raises(ValueError, match="change note"):
        release(new, "1.1.0", previous=original)
    changed = release(
        new, "1.1.0", previous=original, change_note="Correct the publisher URL"
    )
    assert compare_releases(original, changed)["mode"] == "rescore"


def test_editorial_patch_preserves_measurement_but_verifies_exact_input():
    original = release()
    data = dataset()
    data["cases"][0]["derivation"] = "Clarified attribution"
    patch = release(data, "1.0.1", previous=original)
    assert compare_releases(original, patch)["mode"] == "direct"
    with pytest.raises(ValueError, match="changed"):
        verify_release(original, data)


@pytest.mark.parametrize("version", ["1", "1.0", "v1.0.0", "01.0.0", "1.0.0-rc1"])
def test_only_explicit_stable_semver_is_accepted(version):
    with pytest.raises(ValueError, match="MAJOR"):
        release(version=version)


def test_catalog_tampering_and_tag_mismatch_are_rejected(tmp_path, monkeypatch):
    original = catalog_release(require_current_scorer=False)
    for change in ({"tag": "benchmark-v1.0.0"}, {"datasets": {}}, {"current": {}}):
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps({**original, **change}))
        with pytest.raises(ValueError):
            catalog_release(path, require_current_scorer=False)
    broken = deepcopy(original)
    broken["datasets"][next(iter(broken["datasets"]))]["scoring_source_sha256"] = (
        "0" * 64
    )
    from lexbench_websearch import versions

    frozen_hash = next(iter(original["datasets"].values()))["scoring_source_sha256"]
    monkeypatch.setattr(versions, "scoring_source_sha256", lambda: frozen_hash)
    path.write_text(json.dumps(original))
    catalog_release(path)
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="changed"):
        catalog_release(path)


def test_release_cli_writes_once_and_compares_outside_checkout(tmp_path):
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(dataset()))
    output = tmp_path / "release.json"
    call = lambda *args: subprocess.run(
        [sys.executable, "-m", "lexbench_websearch.cli", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    result = call(
        "release",
        "--dataset",
        str(source),
        "--output",
        str(output),
        "--version",
        "1.0.0",
        "--scorer-revision",
        REVISION,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tag"] == "bench-v1.0.0"
    assert (
        json.loads(call("compare-releases", str(output), str(output)).stdout)["mode"]
        == "direct"
    )
    assert call("version").returncode == 0
    with pytest.raises(FileExistsError):
        freeze_release(source, output, version="1.0.0", scorer_revision=REVISION)
    link = tmp_path / "link.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        freeze_release(
            link, tmp_path / "other.json", version="1.0.0", scorer_revision=REVISION
        )


def test_development_inspection_never_updates_or_certifies_an_edition(
    tmp_path, monkeypatch
):
    from lexbench_websearch import versions

    path = tmp_path / "benchmark-release.json"
    source = versions.definition_path("benchmark-release.json").read_bytes()
    path.write_bytes(source)
    monkeypatch.setattr(versions, "scoring_source_sha256", lambda: "f" * 64)
    inspected = catalog_release(path, require_current_scorer=False)
    assert inspected["version"] == json.loads(source)["version"]
    assert inspected["tag"] == json.loads(source)["tag"]
    assert inspected["development"]["scoring_status"] == "unreleased"
    assert path.read_bytes() == source
    with pytest.raises(ValueError, match="scoring contract changed"):
        catalog_release(path)


def test_historical_tag_remains_readable_and_new_tags_are_consistent():
    historical = release()
    historical["tag"] = "bench-1.0.0"
    verify_release(historical, dataset())
    successor = release(version="1.1.0", previous=historical)
    assert successor["tag"] == "bench-v1.1.0"
    assert compare_releases(historical, successor)["mode"] == "direct"


def test_new_editions_reject_the_retired_tag_prefix():
    current = release(version="1.1.0")
    current["tag"] = "bench-1.1.0"
    with pytest.raises(ValueError, match="tag"):
        verify_release(current, dataset())
