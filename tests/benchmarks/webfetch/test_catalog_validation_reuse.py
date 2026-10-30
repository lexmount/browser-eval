"""Repeated annotation controls stay checked without repeating identical scoring work."""

import hashlib
import json
from copy import deepcopy

import pytest

from lexbench_webfetch import benchmark as bench
from lexbench_webfetch import scoring_regressions as regressions
from lexbench_webfetch.versions import catalog_release


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return bench.digest(path)


def encoded_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    root = tmp_path / "definitions"
    suite = root / "sample"
    cases = [{"id": str(index), "url": "https://example.test/" + str(index)} for index in range(3)]
    tasks = {"id": "sample", "cases": cases}
    tasks_hash = write(suite / "tasks.json", tasks)
    dataset_hash = write(suite / "dataset.json", tasks)
    write(
        suite / "dataset-catalog.json",
        {
            "kind": "frozen_dataset_catalog",
            "id": "sample",
            "case_count": 3,
            "dataset_sha256": dataset_hash,
            "cases": [{"id": c["id"]} for c in cases],
        },
    )
    source_hash = dataset_hash
    inputs_hash = write(
        suite / "source-inputs.json",
        {
            "schema_version": 2,
            "kind": "frozen_reference_inputs",
            "id": "sample",
            "catalog": "dataset-catalog.json",
            "catalog_sha256": bench.digest(suite / "dataset-catalog.json"),
            "dataset_sha256": dataset_hash,
            "dataset": "dataset.json",
            "files": {"dataset.json": source_hash},
            "trees": {},
        },
    )
    entry = {
        "id": "sample",
        "dataset_id": "sample",
        "directory": "sample",
        "case_count": 3,
        "files": {p.name: bench.digest(p) for p in suite.iterdir()},
    }
    write(root / "registry.json", {"schema_version": 1, "suites": [entry]})
    controls = []
    for passed in [False, True]:
        controls.append(
            {
                "id": "pass" if passed else "fail",
                "failure_mode": "false-negative" if passed else "false-positive",
                "category": "structured-extraction",
                "root_cause": "A declared content response must be nonempty.",
                "case": {
                    "url": "https://example.test/",
                    "task_kind": "body",
                    "min_content_chars": 1,
                },
                "artifact": "A full article." if passed else "",
                "ok": True,
                "expected_task_pass": passed,
            }
        )
    controls_path = tmp_path / "controls.json"
    write(controls_path, {"schema_version": 1, "cases": controls})
    original_loader = regressions.load_scoring_regressions
    monkeypatch.setattr(
        regressions, "load_scoring_regressions", lambda path=None: original_loader(controls_path)
    )
    changes, reviews = [], []
    new_field = {"required_facts": [{"id": "topic", "alternatives": ["complete article"]}]}
    for case in cases:
        changes.append(
            {
                "suite": "sample",
                "case_id": case["id"],
                "before_sha256": encoded_hash(case),
                "set": new_field,
                "remove": [],
            }
        )
        delta = bench.annotation_changes(case, {**case, **new_field})
        reviews.append(
            {
                "suite": "sample",
                "case_id": case["id"],
                "reason": "The frozen article declares the topic.",
                "changes_sha256": encoded_hash(delta),
                "sources": [
                    {"path": "dataset.json", "sha256": source_hash, "scope": "Frozen reference"}
                ],
                "regression_ids": ["pass", "fail"],
            }
        )
    definition = {
        "schema_version": 1,
        "kind": "score_only_annotation_revision",
        "id": "reviewed",
        "parents": {"sample": {"tasks_sha256": tasks_hash, "source_inputs_sha256": inputs_hash}},
        "regression_ids": ["pass", "fail"],
        "changes": changes,
    }
    revision_hash = write(root / "annotation-revisions/reviewed.json", definition)
    reviews_path = root / "annotation-revisions/reviews.json"
    write(
        reviews_path,
        {
            "schema_version": 1,
            "revisions": {"reviewed": {"revision_sha256": revision_hash, "changes": reviews}},
        },
    )
    release_path = root / "release.json"
    write(
        release_path,
        {
            "schema_version": 1,
            "kind": "benchmark_catalog_release",
            "version": "1.0.0",
            "tag": "bench-v1.0.0",
            "scoring_source_sha256": "c" * 64,
            "scorer_revision": "d" * 40,
            "case_count": 3,
            "suites": [
                {
                    "suite": "sample",
                    "case_count": 3,
                    "dataset_sha256": dataset_hash,
                    **{k: entry[k] for k in ["dataset_id", "directory", "files"]},
                }
            ],
        },
    )
    monkeypatch.setattr(bench, "definitions", lambda: root)
    return release_path, reviews_path, controls_path, suite / "tasks.json"


def check(catalog):
    return catalog_release(catalog[0], require_current_scorer=False)


def test_repeated_control_users_preserve_every_reviewed_task(catalog):
    assert check(catalog)["case_count"] == 3
    result = bench.apply_annotation_revision("sample", bench.read(catalog[3]), "reviewed")
    assert all(
        row["required_facts"][0]["alternatives"] == ["complete article"] for row in result["cases"]
    )


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("source", "source is not bound"),
        ("delta", "unreviewed field changes"),
        ("partial-controls", "accepted and rejected"),
        ("unknown-controls", "controls differ"),
    ],
)
def test_reused_controls_do_not_skip_later_task_validation(catalog, mutation, error):
    assert check(catalog)["case_count"] == 3
    document = bench.read(catalog[1])
    row = document["revisions"]["reviewed"]["changes"][-1]
    if mutation == "source":
        row["sources"][0]["sha256"] = "0" * 64
    elif mutation == "delta":
        row["changes_sha256"] = "0" * 64
    elif mutation == "partial-controls":
        row["regression_ids"] = ["pass"]
    else:
        row["regression_ids"] = ["unknown"]
    write(catalog[1], document)
    with pytest.raises(ValueError, match=error):
        check(catalog)


def test_separate_catalog_operation_reexecutes_changed_control_bytes(catalog):
    assert check(catalog)["case_count"] == 3
    document = bench.read(catalog[2])
    next(row for row in document["cases"] if row["id"] == "pass")["artifact"] = ""
    write(catalog[2], document)
    with pytest.raises(ValueError, match="Annotation regression failed: pass"):
        check(catalog)


@pytest.mark.parametrize("name", ["tasks.json", "dataset.json", "source-inputs.json"])
def test_separate_catalog_operation_rejects_changed_frozen_files(catalog, name):
    assert check(catalog)["case_count"] == 3
    path = catalog[3].parent / name
    document = deepcopy(bench.read(path))
    document["changed"] = True
    write(path, document)
    with pytest.raises(ValueError, match="Changed suite file"):
        check(catalog)
