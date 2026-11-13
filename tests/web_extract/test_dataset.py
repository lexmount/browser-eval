"""Pinned public inputs support clean preparation, offline reuse and corruption checks."""

import gzip
from unittest.mock import patch

import pytest

from harness.web_extract.pipeline import digest, prepare, read_json, save_json, validate_dataset


def distribution(tmp_path, monkeypatch, *, html=None):
    from harness.web_extract import dataset

    source = tmp_path / "public"
    source.mkdir()
    truth = {"one": {"url": "https://example.org/product", "sku": ["SKU-1"]}}
    raw = {
        "dataset/ground-truth.json": b'{"one":{"url":"https://example.org/product","sku":["SKU-1"]}}',
        "html/one.html": "<p>商品 SKU-1</p>".encode() if html is None else html,
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
        "distribution": {"track": "web-extract", "manifest": "runtime.json"},
    }
    profile = tmp_path / "profile.json"
    save_json(profile, {"dataset": pin})

    def binding(track, revision=None):
        assert track == "web-extract"
        if revision not in (None, "b" * 40):
            raise ValueError("Unrecorded dataset revision")
        return {"revision": "b" * 40}

    monkeypatch.setattr(dataset, "binding", binding)
    monkeypatch.setattr(dataset, "definition_path", lambda track, name, revision: source / name)
    return source, profile, manifest


@pytest.mark.parametrize("archived", [False, True])
def test_clean_prepare_uses_public_inputs_and_offline_resume_never_calls_provider(
    tmp_path, monkeypatch, archived
):
    source, profile, expected = distribution(tmp_path, monkeypatch)
    if archived:
        saved = read_json(profile)
        saved["dataset"]["distribution"]["track"] = "web-" + "scraper"
        save_json(profile, saved)
    home = tmp_path / "new-home"
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with patch("harness.web_extract.pipeline.request") as network:
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
        "harness.web_extract.dataset.binding", side_effect=AssertionError("Unexpected Hub access")
    ):
        assert prepare(home, profile) == manifest


@pytest.mark.parametrize("unsafe", [False, True])
@pytest.mark.parametrize("source_track", ["current", "archived", "unrelated"])
def test_shared_publisher_reads_committed_extract_inputs_and_rejects_unknown_binary(
    tmp_path, unsafe, source_track
):
    import json
    import subprocess

    from harness.benchmarks.publication import DatasetRelease

    root = tmp_path / "source"
    definitions = root / "src/lexbench_web-extract/definitions"
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
    (definitions / "README.rst").write_bytes(b"Original upstream documentation\n")
    if unsafe:
        (definitions / "unregistered.bin").write_bytes(b"unexpected binary")

    def commit(directory):
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=test@example.org"]
            + ["commit", "-qm", "Fixture"],
        ):
            subprocess.run(["git", *args], cwd=directory, capture_output=True, check=True)
        head = ["git", "rev-parse", "HEAD"]
        return subprocess.check_output(head, cwd=directory, text=True).strip()

    revision = commit(root)
    (definitions / "evaluate.py").write_bytes(b"# uncommitted edit\n")
    if unsafe:
        # Source repositories may only publish registered definition file types.
        with pytest.raises(ValueError, match="Unexpected definition file"):
            DatasetRelease.from_git(root, "web-extract", revision)
        return
    release = DatasetRelease.from_git(root, "web-extract", revision)
    assert release.files["definitions/evaluate.py"] == b"# original evaluator\n"
    hub = tmp_path / "hub"
    release.build(hub)
    from harness.benchmarks.data import source_name_migrations

    original_track = next(iter(source_name_migrations()["tracks"]))
    source_name = {"current": "web-extract", "archived": original_track, "unrelated": "webfetch"}[
        source_track
    ]
    manifest = json.loads((hub / "manifest.json").read_bytes())
    save_json(hub / "manifest.json", {**manifest, "track": source_name})
    revision = commit(hub)
    (hub / "definitions/evaluate.py").write_bytes(b"# uncommitted edit\n")
    if source_track == "unrelated":
        with pytest.raises(ValueError, match="does not match the requested benchmark track"):
            DatasetRelease.from_git(hub, "web-extract", revision)
        return
    rebuilt = DatasetRelease.from_git(hub, "web-extract", revision)
    assert rebuilt.files["definitions/evaluate.py"] == b"# original evaluator\n"
    out = tmp_path / "built"
    result = rebuilt.build(out)
    assert result["datasets"]["zyte-products-140"]["count"] == 1
    assert (
        json.loads((out / "data/zyte-products-140.jsonl").read_text(encoding="utf-8"))["id"]
        == "one"
    )
    assert (out / "definitions.tar.gz").is_file()


@pytest.mark.parametrize("damage", [None, "other-html", "other-upstream"])
@pytest.mark.parametrize("archived", [False, True])
def test_report_distribution_requires_the_same_frozen_inputs(
    tmp_path, monkeypatch, damage, archived
):
    from harness.web_extract.dataset import distribution_identity

    source, profile, manifest = distribution(tmp_path, monkeypatch)
    home = tmp_path / "home"
    prepare(home, profile)
    save_json(source / "runtime/manifest.json", manifest)
    if damage == "other-html":
        manifest["files"]["html/one.html"]["sha256"] = "0" * 64
    elif damage == "other-upstream":
        manifest["commit"] = "c" * 40
    save_json(source / "runtime/manifest.json", manifest)
    from harness.web_extract import dataset

    original = dataset.binding
    monkeypatch.setattr(
        dataset,
        "binding",
        lambda *a: {
            **original(*a),
            "repository": "Lexmount/LexBench-Zyte-Product-Extraction",
            "archive": {"sha256": "f" * 64},
        },
    )
    selection = {"track": "web-extract", "revision": "b" * 40}
    if archived:
        selection["track"] = "web-" + "scraper"
    if damage:
        with pytest.raises(ValueError, match="distribution"):
            distribution_identity(home, read_json(profile)["dataset"], selection)
    else:
        result = distribution_identity(home, read_json(profile)["dataset"], selection)
        assert result["url"].endswith("/tree/" + "b" * 40)
        assert result["archive_sha256"] == "f" * 64


def test_default_distribution_has_a_real_complete_immutable_binding():
    from harness.benchmarks.data import binding
    from harness.web_extract.pipeline import PROFILE

    profile = read_json(PROFILE)
    selected = binding(profile["dataset"]["distribution"]["track"])
    assert len(selected["revision"]) == 40
    assert selected["repository"] == "Lexmount/LexBench-Zyte-Product-Extraction"
    assert selected["datasets"][selected["default_dataset"]]["count"] == 140
    assert selected["archive"]["size"] > 0
    assert len(selected["archive"]["sha256"]) == 64
    assert "definitions/runtime/manifest.json" in selected["files"]
    for name, expected in profile["dataset"]["files"].items():
        assert selected["files"]["definitions/upstream/" + name]["sha256"] == expected
    assert (
        sum(name.startswith("definitions/upstream/dataset/html/") for name in selected["files"])
        == 140
    )


@pytest.mark.parametrize("symlink", ["ancestor", "dangling-leaf", "legacy-leaf"])
def test_historical_cache_symlinks_are_rejected_before_any_write(tmp_path, monkeypatch, symlink):
    from harness.web_extract.pipeline import PROFILE, reproduce

    _, profile, _ = distribution(tmp_path, monkeypatch)
    home = tmp_path / "home"
    prepare(home, profile)
    external = tmp_path / "external"
    external.mkdir()
    method, prediction = next(iter(read_json(PROFILE)["historical_predictions"].items()))
    if symlink == "ancestor":
        (home / "historical-predictions").symlink_to(external, target_is_directory=True)
    else:
        link = (
            home / "dataset" / prediction["path"]
            if symlink == "legacy-leaf"
            else home / "historical-predictions" / (method + ".json")
        )
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(external / "must-not-be-created.json")
    with (
        patch("harness.web_extract.pipeline.official_scorer", return_value={}),
        patch("harness.web_extract.pipeline.request") as network,
        pytest.raises(ValueError, match="Unsafe historical prediction cache"),
    ):
        reproduce(home)
    network.assert_not_called()
    assert list(external.iterdir()) == []


def test_prior_dataset_names_resolve_to_the_current_canonical_repository():
    from harness.benchmarks.data import canonical_repository, source_name_migrations

    for original, canonical in source_name_migrations().get("repositories", {}).items():
        assert canonical_repository(original) == canonical

    current = "Lexmount/LexBench-Zyte-Product-Extraction"
    for name in (
        current,
        "Lexmount/zyte-product-extraction-benchmark",
    ):
        assert canonical_repository(name) == current


def test_data_selection_exposes_only_the_current_track_name():
    from harness.benchmarks.data import binding

    assert binding("web-extract")["datasets"]["zyte-products-140"]["count"] == 140
    from harness.benchmarks.data import source_name_migrations

    names = source_name_migrations()["schemas"]
    original = next(k for k, v in names.items() if v == "web-extract-public-snapshots/2")
    retired = original.removesuffix("-public-snapshots/2")
    with pytest.raises(KeyError):
        binding(retired)


@pytest.mark.parametrize("raw", ["<p>商品 SKU-1</p>".encode(), b"<p>SKU-\xff</p>"])
def test_undeclared_utf8_archive_is_uploaded_without_changing_its_text(
    tmp_path, monkeypatch, raw, stubbed_serial_collection
):
    from harness.web_extract import pipeline

    _, profile, _ = distribution(tmp_path, monkeypatch, html=raw)
    home = tmp_path / "home"
    if b"\xff" in raw:
        with pytest.raises(ValueError, match="Frozen HTML must be UTF-8"):
            prepare(home, profile)
        assert not (tmp_path / "run").exists()
        return
    prepare(home, profile)
    monkeypatch.setattr(pipeline, "selected_profile", lambda _: profile)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "synthetic")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic")
    sent = []

    def provider(method, html, *args, **options):
        sent.append(html)
        return {"http_status": 200, "response": {"result": {"sku": "SKU-1"}}}

    monkeypatch.setattr(pipeline, "provider_call", provider)
    pipeline.probe(home, ["cloudflare-json"], full=True, run_dir=tmp_path / "run")
    assert sent == [raw.decode("utf-8")]
