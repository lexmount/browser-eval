"""Published source editions are independent of sample size and provider choice."""

from pathlib import Path

import pytest

from harness.web_extract import dataset, pipeline
from tests.web_extract.test_records import definition


def registered_source(tmp_path, monkeypatch, *, count=3, mode="snapshot"):
    source = definition(tmp_path, mode=mode)
    cohort = pipeline.read_json(source)
    cohort["cases"] = [dict(cohort["cases"][0], id=f"item-{i}") for i in range(count)]
    pipeline.save_json(source, cohort)
    revision = "a" * 40
    selected = {
        "repository": "example/fourth-dataset",
        "revision": revision,
        "archive": {"sha256": "b" * 64},
        "default_dataset": "arbitrary-population",
        "datasets": {"arbitrary-population": {
            "path": source.name, "sha256": pipeline.digest(source.read_bytes()), "count": count,
        }},
        "files": {"definitions/" + p.name: {} for p in tmp_path.iterdir() if p.is_file()},
    }
    # Portable distributions cannot use local absolute filenames.
    for case in cohort["cases"]:
        case["input"] = "source.html"
    pipeline.save_json(source, cohort)
    selected["datasets"]["arbitrary-population"]["sha256"] = pipeline.digest(source.read_bytes())
    monkeypatch.setattr(dataset, "_registered_sources", lambda: {
        "fourth": {"adapter": "records"},
        "unpublished-next-source": {"adapter": "records"},
    })

    def binding(name, revision=None):
        assert name == "fourth"
        if revision not in (None, selected["revision"]):
            raise ValueError("Dataset revision is not recorded")
        return selected

    monkeypatch.setattr(dataset, "binding", binding)
    monkeypatch.setattr(dataset, "definition_path", lambda *args: tmp_path)
    return source, selected


@pytest.mark.parametrize("count,mode", [(1, "live"), (7, "snapshot")])
def test_public_prepare_accepts_additional_dataset_and_edition(tmp_path, monkeypatch, count, mode):
    source, selected = registered_source(tmp_path, monkeypatch, count=count, mode=mode)
    home = tmp_path / "prepared"
    result = pipeline.prepare(home, dataset="fourth", revision=selected["revision"])
    assert result["cases"] == count
    assert dataset.input_mode(home) == mode
    profile = pipeline.read_json(home / "profile.json")
    assert profile["source_distribution"]["dataset"] == "fourth"
    assert profile["source_distribution"]["revision"] == selected["revision"]
    assert profile["source_distribution"]["archive_sha256"] == selected["archive"]["sha256"]
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    assert len(truth) == count
    assert all(row["values"] == {"name": ["Ada"]} for row in truth.values())
    # Preparing inputs has no participant selection and preserves the original definition.
    assert "methods" not in profile
    assert pipeline.read_json(source)["cases"][0]["values"] == {"name": ["Ada"]}


@pytest.mark.parametrize(
    "defect", ["revision", "hash", "count", "absolute", "traversal", "missing"]
)
def test_registered_prepare_rejects_changed_or_unbound_inputs(tmp_path, monkeypatch, defect):
    source, selected = registered_source(tmp_path, monkeypatch)
    if defect == "hash":
        source.write_bytes(source.read_bytes() + b" ")
    elif defect == "count":
        selected["datasets"]["arbitrary-population"]["count"] += 1
    elif defect in {"absolute", "traversal", "missing"}:
        value = pipeline.read_json(source)
        value["cases"][0]["input"] = {
            "absolute": "/tmp/unbound.html", "traversal": "../unbound.html",
            "missing": "unbound.html",
        }[defect]
        pipeline.save_json(source, value)
        selected["datasets"]["arbitrary-population"]["sha256"] = pipeline.digest(
            source.read_bytes()
        )
    with pytest.raises(ValueError, match="recorded|pinned population|locked archive"):
        pipeline.prepare(tmp_path / "prepared", dataset="fourth",
                         revision="c" * 40 if defect == "revision" else None)
    assert not (tmp_path / "prepared/dataset").exists()


def test_dataset_listing_uses_registry_not_a_fixed_name_list(tmp_path, monkeypatch):
    actual_read = dataset.read_json
    monkeypatch.setattr(dataset, "read_json", lambda path: {
        "fourth": {"evaluation_track": "web-extract", "adapter": "records",
                   "pretty_name": "Fourth"},
        "unrelated": {"evaluation_track": "webfetch"},
    } if Path(path).name == "metadata.json" else actual_read(path))
    monkeypatch.setattr(dataset, "binding", lambda name: {
        "repository": "example/fourth", "revision": "a" * 40,
        "datasets": {"growing": {"count": 1234}},
    })
    from harness.web_extract.cli import main

    # Exercise discovery through the supported command as well as the Python API.
    main(["datasets"])
    assert dataset.registered_datasets()["fourth"]["datasets"]["growing"]["count"] == 1234


def test_local_source_and_registered_selection_are_unambiguous(tmp_path):
    with pytest.raises(ValueError, match="registered dataset or"):
        pipeline.prepare(tmp_path, dataset="fourth", source=tmp_path / "cohort.json")
    with pytest.raises(ValueError, match="requires a registered"):
        pipeline.prepare(tmp_path, revision="a" * 40)


def test_registered_dataset_can_select_more_than_three_capable_methods(tmp_path, monkeypatch):
    from harness.web_extract.certification import create_plan

    registered_source(tmp_path, monkeypatch, count=5, mode="live")
    home = tmp_path / "prepared"
    pipeline.prepare(home, dataset="fourth")
    methods = ["browserbase-json", "firecrawl-json", "scrapegraph-extract", "oxylabs-json"]
    plan = create_plan(home, methods, tmp_path / "plan.json")
    assert set(plan["participants"]) == set(methods)
    assert plan["matrix_size"] == 20
    with pytest.raises(ValueError, match="arbitrary-schema"):
        create_plan(home, ["diffbot-product"], tmp_path / "invalid-plan.json")
