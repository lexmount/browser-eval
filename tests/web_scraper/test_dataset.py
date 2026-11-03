"""Pinned public inputs support clean preparation, offline reuse and corruption checks."""

import gzip
from unittest.mock import patch

import pytest

from harness.web_scraper.pipeline import digest, prepare, read_json, save_json, validate_dataset


def distribution(tmp_path, monkeypatch):
    from harness.web_scraper import dataset

    source = tmp_path / "public"
    source.mkdir()
    truth = {"one": {"url": "https://example.org/product", "sku": ["SKU-1"]}}
    raw = {
        "dataset/ground-truth.json": b'{"one":{"url":"https://example.org/product","sku":["SKU-1"]}}',
        "html/one.html": "<p>商品 SKU-1</p>".encode(),
        "evaluate.py": b"# fixed upstream evaluator\n",
    }
    manifest = {
        "commit": "a" * 40,
        "cases": len(truth),
        "files": {n: {"sha256": digest(b), "bytes": len(b)} for n, b in raw.items()},
        "source_files": {},
    }
    for i, (name, body) in enumerate(raw.items()):
        path = source / (str(i) + ".gz")
        path.write_bytes(gzip.compress(body))
        manifest["source_files"][name] = {"path": path.name, "compression": "gzip"}
    save_json(source / "runtime.json", manifest)
    pin = {
        "commit": "a" * 40,
        "cases": 1,
        "files": {"evaluate.py": digest(raw["evaluate.py"])},
        "inventory_sha256": "0" * 64,
        "distribution": {"track": "web-scraper", "manifest": "runtime.json"},
    }
    profile = tmp_path / "profile.json"
    save_json(profile, {"dataset": pin})

    def binding(track, revision=None):
        assert track == "web-scraper"
        if revision not in (None, "b" * 40):
            raise ValueError("Unrecorded dataset revision")
        return {"revision": "b" * 40}

    monkeypatch.setattr(dataset, "binding", binding)
    monkeypatch.setattr(dataset, "definition_path", lambda track, name, revision: source / name)
    return source, profile, manifest


def test_clean_prepare_uses_public_inputs_and_offline_resume_never_calls_provider(
    tmp_path, monkeypatch
):
    source, profile, expected = distribution(tmp_path, monkeypatch)
    home = tmp_path / "new-home"
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with patch("harness.web_scraper.pipeline.request") as network:
        result = prepare(home, profile)
        assert result["distribution_revision"] == "b" * 40
        assert read_json(home / "dataset/dataset/ground-truth.json")["one"]["sku"] == ["SKU-1"]
        assert (home / "dataset/html/one.html").read_text(encoding="utf-8") == "<p>商品 SKU-1</p>"
        assert not (home / "dataset/dataset/output").exists()
        assert prepare(home, profile) == result
        network.assert_not_called()


@pytest.mark.parametrize("damage", ["html", "manifest", "revision"])
def test_public_input_corruption_is_rejected_without_replacing_originals(
    tmp_path, monkeypatch, damage
):
    _, profile, _ = distribution(tmp_path, monkeypatch)
    home = tmp_path / "home"
    prepare(home, profile)
    path = home / "dataset/manifest.json"
    if damage == "html":
        (home / "dataset/html/one.html").write_bytes(b"changed")
    else:
        manifest = read_json(path)
        if damage == "manifest":
            manifest["files"]["html/one.html"]["sha256"] = digest(b"changed")
        else:
            manifest["distribution_revision"] = "c" * 40
        save_json(path, manifest)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        validate_dataset(home, profile)
    assert path.read_bytes() == original


def test_failed_materialization_does_not_leave_partial_dataset(tmp_path, monkeypatch):
    source, profile, manifest = distribution(tmp_path, monkeypatch)
    (source / manifest["source_files"]["html/one.html"]["path"]).write_bytes(
        gzip.compress(b"wrong")
    )
    home = tmp_path / "home"
    with pytest.raises(ValueError, match="materialization changed"):
        prepare(home, profile)
    assert not (home / "dataset").exists()


def test_existing_hash_bound_upstream_inputs_remain_valid_without_hub(tmp_path):
    home = tmp_path / "legacy"
    root = home / "dataset"
    files = {"dataset/ground-truth.json": b'{"one":{}}', "html/one.html": b"original"}
    manifest = {"commit": "a" * 40, "cases": 1, "files": {}}
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        manifest["files"][name] = {"sha256": digest(raw), "bytes": len(raw)}
    save_json(root / "manifest.json", manifest)
    import json

    inventory = digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    profile = tmp_path / "profile.json"
    save_json(profile, {"dataset": {"commit": "a" * 40, "cases": 1, "inventory_sha256": inventory}})
    with patch(
        "harness.web_scraper.dataset.binding", side_effect=AssertionError("Unexpected Hub access")
    ):
        assert prepare(home, profile) == manifest


@pytest.mark.parametrize("unsafe", [False, True])
def test_shared_publisher_reads_committed_scraper_inputs_and_rejects_unknown_binary(
    tmp_path, unsafe
):
    import json
    import subprocess

    from harness.benchmarks.publication import DatasetRelease

    root = tmp_path / "source"
    definitions = root / "definitions"
    definitions.mkdir(parents=True)
    tasks = b'{"cases":[{"id":"one","url":"https://example.org"}]}'
    (definitions / "tasks.json").write_bytes(tasks)
    save_json(
        definitions / "registry.json",
        {
            "datasets": {
                "zyte-products-140": {
                    "path": "tasks.json",
                    "count": 1,
                    "sha256": digest(tasks),
                }
            }
        },
    )
    (definitions / "page.html.gz").write_bytes(gzip.compress(b"frozen page"))
    (definitions / "evaluate.py").write_bytes(b"# original evaluator\n")
    if unsafe:
        (definitions / "unregistered.bin").write_bytes(b"unexpected binary")
    save_json(
        root / "manifest.json", {"schema": "browser-eval-dataset-release/1", "track": "web-scraper"}
    )
    for args in (
        ["init"],
        ["add", "."],
        ["-c", "user.name=Fixture", "-c", "user.email=test@example.org", "commit", "-m", "Fixture"],
    ):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    (definitions / "evaluate.py").write_bytes(b"# uncommitted edit\n")
    if unsafe:
        with pytest.raises(ValueError, match="Unexpected definition file"):
            DatasetRelease.from_git(root, "web-scraper", revision)
    else:
        release = DatasetRelease.from_git(root, "web-scraper", revision)
        assert release.files["definitions/evaluate.py"] == b"# original evaluator\n"
        out = tmp_path / "built"
        result = release.build(out)
        assert result["datasets"]["zyte-products-140"]["count"] == 1
        assert (
            json.loads((out / "data/zyte-products-140.jsonl").read_text(encoding="utf-8"))["id"]
            == "one"
        )
        assert (out / "definitions.tar.gz").is_file()
