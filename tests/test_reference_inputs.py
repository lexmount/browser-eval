"""Reference-input boundaries independent of any third-party historical dataset."""

import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.reference_inputs import (
    check_reference_input_boundary,
    import_inputs,
    input_tree,
    load_manifest,
    main,
    read_dataset_bytes,
    require_mutable_inputs,
)
from harness.web_fetch.storage import require_canonical_inputs


def sha(value):
    return hashlib.sha256(value).hexdigest()


def external_dataset(inputs):
    dataset, manifest_path, source, manifest = inputs
    original = dataset.read_bytes()
    (source / "dataset.json").write_bytes(original)
    data = json.loads(original)
    definition = sha(
        json.dumps(
            data["cases"][0], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    catalog = {
        **{key: data[key] for key in ("id", "name", "coverage_plan")},
        "kind": "frozen_dataset_catalog",
        "dataset_sha256": sha(original),
        "case_count": 1,
        "cases": [
            {"id": "case-a", "url": "https://example.invalid", "definition_sha256": definition}
        ],
    }
    (dataset.parent / "dataset-catalog.json").write_text(json.dumps(catalog))
    manifest.update(
        schema_version=2,
        catalog="dataset-catalog.json",
        catalog_sha256=sha((dataset.parent / "dataset-catalog.json").read_bytes()),
        files={"dataset.json": sha(original)},
    )
    manifest_path.write_text(json.dumps(manifest))
    dataset.unlink()
    return original


def test_catalog_is_non_executable_and_cannot_misdescribe_frozen_tasks(inputs):
    from harness.web_fetch.config import load_dataset

    dataset, manifest_path, source, manifest = inputs
    external_dataset(inputs)
    catalog_path = dataset.parent / "dataset-catalog.json"
    with pytest.raises(ValueError, match="dataset.tool"):
        load_dataset(catalog_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["cases"][0]["id"] = "another-task"
    catalog_path.write_text(json.dumps(catalog))
    manifest["catalog_sha256"] = sha(catalog_path.read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="catalog metadata differs"):
        import_inputs(manifest_path, source)


def test_external_dataset_preserves_frozen_bytes_and_canonical_evidence_contract(inputs, tmp_path):
    from harness.web_fetch.config import load_dataset

    dataset, manifest_path, source, _ = inputs
    original = external_dataset(inputs)
    with pytest.raises(FileNotFoundError, match="not installed"):
        load_dataset(dataset)
    output = import_inputs(manifest_path, source)
    assert read_dataset_bytes(dataset) == original
    assert load_dataset(dataset)[1][0].id == "case-a"
    assert input_tree(dataset, "sources") == output / "sources"
    evidence = tmp_path / "evidence"
    import shutil

    shutil.copytree(output, evidence)
    require_canonical_inputs(evidence, dataset.parent)
    check_reference_input_boundary(dataset.parent)
    dataset.write_text("unbound local fallback")
    assert read_dataset_bytes(dataset) == original
    with pytest.raises(ValueError, match="duplicated"):
        check_reference_input_boundary(dataset.parent)


def test_dataset_only_inputs_preserve_bytes_without_inventing_source_trees(inputs):
    dataset, manifest_path, source, manifest = inputs
    original = external_dataset(inputs)
    manifest["trees"] = {}
    manifest_path.write_text(json.dumps(manifest))
    output = import_inputs(manifest_path, source)
    assert read_dataset_bytes(dataset) == original
    assert sorted(path.name for path in output.iterdir()) == ["dataset.json"]
    assert input_tree(dataset, "sources") == output / "sources"
    assert not (output / "sources").exists()
    main(["--manifest", str(manifest_path), "--check"])
    check_reference_input_boundary(dataset.parent)


@pytest.mark.parametrize("location", ["checkout", "installed"])
def test_dataset_only_inputs_reject_undeclared_source_trees(inputs, location):
    dataset, manifest_path, source, manifest = inputs
    external_dataset(inputs)
    manifest["trees"] = {}
    manifest_path.write_text(json.dumps(manifest))
    output = import_inputs(manifest_path, source)
    tree = (dataset.parent if location == "checkout" else output) / "sources"
    tree.mkdir()
    (tree / "private.txt").write_text("undeclared source material")
    with pytest.raises(ValueError, match="[Uu]ndeclared|duplicated"):
        input_tree(dataset, "sources")
    if location == "checkout":
        with pytest.raises(ValueError, match="duplicated"):
            check_reference_input_boundary(dataset.parent)


@pytest.mark.parametrize("destination", ["checkout", "installed"])
def test_dataset_rebuild_refuses_a_frozen_output_before_reading_source_material(inputs, destination):
    from harness.web_fetch.risk_control_dataset import build

    dataset, manifest_path, source, _ = inputs
    original = external_dataset(inputs)
    installed = import_inputs(manifest_path, source)
    target = dataset if destination == "checkout" else installed / "dataset.json"
    with pytest.raises(ValueError, match="immutable"):
        build(target)
    assert not dataset.exists()
    assert (installed / "dataset.json").read_bytes() == original


def test_certified_native_preparation_uses_bound_external_dataset_bytes(inputs, tmp_path, monkeypatch):
    from harness.web_fetch.certification import main as certified_main

    dataset, manifest_path, source, _ = inputs
    original = external_dataset(inputs)
    import_inputs(manifest_path, source)
    work = tmp_path / 'native-work'

    class CaptureIntercepted(Exception):
        pass

    commands = []

    def intercept(command):
        commands.append(command)
        raise CaptureIntercepted

    monkeypatch.setattr('harness.web_fetch.certification.run', intercept)
    with pytest.raises(CaptureIntercepted):
        certified_main(['--dataset', str(dataset), '--output', str(tmp_path / 'native-output'),
                        '--work-root', str(work), '--providers', 'fixture',
                        '--case-ids', 'case-a', '--retrieval-mode', 'native'])
    prepared = json.loads((work / 'input-dataset.json').read_bytes())
    assert prepared['comparison']['input_dataset_sha256'] == sha(original)
    assert prepared['cases'] == json.loads(original)['cases']
    assert not dataset.exists()
    assert read_dataset_bytes(dataset) == original
    assert Path(commands[0][1]).name == 'collect_source_evidence.py'


@pytest.mark.parametrize("with_source_tree", [True, False])
def test_public_cli_runs_bound_dataset_and_keeps_original_manifest_hash(
    inputs, tmp_path, with_source_tree
):
    from harness.reporting.links import check
    from harness.web_fetch.cli import build_parser, command_run

    dataset, manifest_path, source, manifest = inputs
    original = external_dataset(inputs)
    if not with_source_tree:
        manifest["trees"] = {}
        manifest_path.write_text(json.dumps(manifest))
    import_inputs(manifest_path, source)
    env = tmp_path / "empty.env"
    env.write_text("")
    output = tmp_path / "run"
    args = build_parser().parse_args(
        [
            "run",
            "--dataset",
            str(dataset),
            "--env-file",
            str(env),
            "--output",
            str(output),
            "--delay",
            "0",
            "--fail-on-failure",
        ]
    )
    assert command_run(args) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["dataset_sha256"] == sha(original)
    scoped = json.loads((output / "dataset.json").read_text())
    assert scoped.pop("comparison")["publication_state"] == "diagnostic_not_for_release"
    assert scoped == json.loads(original)
    records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(records) == 1 and records[0]["metrics"]["content_pass"]
    assert (output / records[0]["artifact_path"]).read_bytes()
    check(output / "report")


def test_independent_source_capture_reads_the_complete_bound_task(inputs, tmp_path, monkeypatch):
    from harness.web_fetch import source_capture

    dataset, manifest_path, source, _ = inputs
    original = external_dataset(inputs)
    import_inputs(manifest_path, source)
    seen = []

    def capture(case, output, resolved_hosts):
        seen.append(case)
        return {"case_id": case["id"], "status": 200}

    monkeypatch.setattr(source_capture, "capture", capture)
    results = source_capture.collect(dataset, tmp_path / "captures", workers=1)
    assert seen == json.loads(original)["cases"]
    assert results == [{"case_id": "case-a", "status": 200}]


@pytest.mark.parametrize("damage", ["dataset", "catalog", "missing", "symlink"])
def test_external_dataset_binding_rejects_changed_missing_or_redirected_inputs(inputs, damage):
    dataset, manifest_path, source, _ = inputs
    external_dataset(inputs)
    output = import_inputs(manifest_path, source)
    if damage == "catalog":
        (dataset.parent / "dataset-catalog.json").write_text("{}")
    elif damage == "dataset":
        (output / "dataset.json").write_text("{}")
    elif damage == "missing":
        (output / "dataset.json").unlink()
    else:
        (output / "dataset.json").unlink()
        (output / "dataset.json").symlink_to(source / "dataset.json")
    with pytest.raises((ValueError, FileNotFoundError)):
        read_dataset_bytes(dataset)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    dataset = checkout / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "synthetic-inputs",
                "name": "Synthetic inputs",
                "tool": "web_fetch",
                "coverage_plan": {"web": ["fixture"]},
                "cases": [
                    {
                        "id": "case-a",
                        "name": "Fixture",
                        "url": "https://example.invalid",
                        "category": "web",
                        "expected_any": ["fixture"],
                    }
                ],
            }
        )
    )
    source = tmp_path / "approved-copy"
    (source / "sources/case-a").mkdir(parents=True)
    (source / "sources/case-a/reference.txt").write_bytes(b"independent fixture")
    (source / "sources/case-a/source.html").write_bytes(b"<main>independent fixture</main>")
    files = {
        path.relative_to(source / "sources").as_posix(): sha(path.read_bytes())
        for path in source.rglob("*")
        if path.is_file()
    }
    manifest = {
        "schema_version": 1,
        "kind": "frozen_reference_inputs",
        "id": "synthetic-v1",
        "dataset": "dataset.json",
        "dataset_sha256": sha(dataset.read_bytes()),
        "trees": {"sources": files},
    }
    manifest_path = checkout / "source-inputs.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "private-data"))
    return dataset, manifest_path, source, manifest


def test_import_is_allowlisted_and_dataset_adjacent_repository_copy_is_not_used(inputs):
    dataset, manifest_path, source, manifest = inputs
    (source / "private-review.json").write_text("not an input")
    (source / "sources/case-a/private-worker-log.txt").write_text("not an input")
    output = import_inputs(manifest_path, source)
    assert input_tree(dataset, "sources") == output / "sources"
    assert sorted(
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    ) == ["sources/" + name for name in sorted(manifest["trees"]["sources"])]
    assert (output / "sources/case-a/reference.txt").read_bytes() == b"independent fixture"
    # A repository-side copy cannot mask changed or absent externally bound data.
    (dataset.parent / "sources/case-a").mkdir(parents=True)
    (dataset.parent / "sources/case-a/reference.txt").write_text("unbound fallback")
    (output / "sources/case-a/reference.txt").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        input_tree(dataset, "sources")


def test_missing_installed_inputs_do_not_fall_back_to_checkout(inputs):
    dataset, _, _, _ = inputs
    (dataset.parent / "sources/case-a").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="not installed"):
        input_tree(dataset, "sources")


@pytest.mark.parametrize("damage", ["changed", "missing", "symlink"])
def test_invalid_source_cannot_leave_a_partial_import(inputs, damage):
    dataset, manifest_path, source, _ = inputs
    path = source / "sources/case-a/reference.txt"
    if damage == "changed":
        path.write_text("different")
    elif damage == "missing":
        path.unlink()
    else:
        path.unlink()
        path.symlink_to(source / "sources/case-a/source.html")
    with pytest.raises((ValueError, FileNotFoundError)):
        import_inputs(manifest_path, source)
    private = dataset.parent.parent / "private-data/reference-inputs"
    assert not (private / "synthetic-v1").exists()
    assert not list(private.glob(".reference-inputs-*"))


@pytest.mark.parametrize("damage", ["missing", "extra", "symlink"])
def test_installed_inventory_is_exact_and_rejects_missing_extra_or_linked_files(inputs, damage):
    dataset, manifest_path, source, _ = inputs
    output = import_inputs(manifest_path, source)
    path = output / "sources/case-a/reference.txt"
    if damage == "missing":
        path.unlink()
    elif damage == "extra":
        (output / "sources/extra.txt").write_text("unbound")
    else:
        path.unlink()
        path.symlink_to(source / "sources/case-a/reference.txt")
    with pytest.raises(ValueError):
        input_tree(dataset, "sources")


@pytest.mark.parametrize("replacement", ["missing", "file"])
def test_corrupted_installed_tree_is_not_treated_as_optional_absent_data(inputs, replacement):
    import shutil

    dataset, manifest_path, source, _ = inputs
    output = import_inputs(manifest_path, source)
    shutil.rmtree(output / "sources")
    if replacement == "file":
        (output / "sources").write_text("wrong tree type")
    with pytest.raises(ValueError, match="missing or is not a directory"):
        input_tree(dataset, "sources")


def test_cli_check_preserves_existing_bytes_and_refuses_overwrite(inputs):
    dataset, manifest_path, source, _ = inputs
    main(["--manifest", str(manifest_path), "--source", str(source)])
    output = input_tree(dataset, "sources")
    before = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    main(["--manifest", str(manifest_path), "--check"])
    with pytest.raises(ValueError, match="already exists"):
        main(["--manifest", str(manifest_path), "--source", str(source)])
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize("damage", ["dataset", "path", "hash", "version"])
def test_manifest_rejects_wrong_dataset_escaping_paths_and_invalid_bindings(inputs, damage):
    dataset, manifest_path, _, manifest = inputs
    if damage == "dataset":
        dataset.write_text("{}")
    elif damage == "path":
        manifest["trees"]["sources"] = {"../../escape": "a" * 64}
    elif damage == "hash":
        manifest["trees"]["sources"] = {"safe": "invalid"}
    else:
        manifest["schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_manifest(manifest_path, dataset)


def test_explicit_custom_dataset_uses_its_own_sources_without_global_state(tmp_path):
    dataset = tmp_path / "custom.json"
    dataset.write_text("{}")
    assert input_tree(dataset, "sources") == tmp_path / "sources"
    require_mutable_inputs(dataset)


def test_canonical_validation_compares_evidence_against_external_inputs(inputs, tmp_path):
    dataset, manifest_path, source, _ = inputs
    import_inputs(manifest_path, source)
    evidence = tmp_path / "measured"
    (evidence / "sources/case-a").mkdir(parents=True)
    (evidence / "dataset.json").write_bytes(dataset.read_bytes())
    for path in (source / "sources/case-a").iterdir():
        (evidence / "sources/case-a" / path.name).write_bytes(path.read_bytes())
    require_canonical_inputs(evidence, dataset.parent)
    (evidence / "sources/case-a/reference.txt").write_text("different")
    with pytest.raises(ValueError, match="canonical input tree"):
        require_canonical_inputs(evidence, dataset.parent)


@pytest.mark.parametrize("owner", ["merge", "prepare", "unavailable", "promote"])
def test_reference_update_commands_reject_pinned_inputs_before_writes(inputs, tmp_path, owner):
    dataset, _, _, _ = inputs
    before = dataset.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        if owner == "merge":
            from scripts.merge_reference_updates import merge

            merge(dataset, [], set())
        elif owner == "prepare":
            from scripts.prepare_references import prepare

            prepare(dataset)
        elif owner == "unavailable":
            from scripts.apply_reference_unavailability import apply

            apply(dataset, tmp_path / "absent", set())
        else:
            from scripts.promote_reference_candidate import promote

            promote(
                canonical_path=dataset,
                candidate_path=tmp_path / "absent",
                captures=tmp_path / "absent",
                receipt_path=tmp_path / "absent",
                receipt_target=tmp_path / "absent",
            )
    assert dataset.read_bytes() == before


def test_runner_rejects_missing_bound_inputs_before_provider_preflight_or_output(
    inputs, tmp_path, monkeypatch
):
    from harness.web_fetch import benchmark_runner as run_benchmark

    dataset, _, _, _ = inputs
    monkeypatch.setattr(run_benchmark, "load_runtime_env", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing bound inputs must be rejected before provider construction or calls")

    monkeypatch.setattr(run_benchmark, "build_provider", forbidden)
    output = tmp_path / "run"
    with pytest.raises(FileNotFoundError):
        run_benchmark.main(
            [
                "--dataset",
                str(dataset),
                "--output",
                str(output),
                "--providers",
                "fixture",
                "--measure-only",
            ]
        )
    assert not output.exists()


def test_cohort_copies_bound_external_list_references(inputs, tmp_path):
    from harness.web_fetch import benchmark_runner

    dataset, manifest_path, source, manifest = inputs
    reference = source / 'list-references/case-a.json'
    reference.parent.mkdir()
    reference.write_bytes(b'{"source":"independent synthetic input"}')
    manifest['trees']['list-references'] = {'case-a.json': sha(reference.read_bytes())}
    external_dataset(inputs)
    import_inputs(manifest_path, source)
    config = tmp_path / 'providers.json'
    config.write_text(json.dumps({'providers': {'fixture': {'type': 'fixture'}}}))
    env = tmp_path / 'empty.env'
    env.write_text('')
    output = tmp_path / 'run'
    benchmark_runner.main(['--dataset', str(dataset), '--provider-config', str(config),
        '--providers', 'fixture', '--env-file', str(env), '--output', str(output),
        '--measure-only', '--workers', '1'])
    assert not (dataset.parent / 'list-references').exists()
    assert (output / 'list-references/case-a.json').read_bytes() == reference.read_bytes()
    assert (output / 'sources/case-a/reference.txt').read_bytes() == b'independent fixture'


def test_repository_gate_rejects_reintroduced_external_source_copies(inputs):
    from harness.web_fetch.reference_inputs import check_reference_input_boundary

    dataset, _, _, _ = inputs
    check_reference_input_boundary(dataset.parent)
    (dataset.parent / "sources").mkdir()
    with pytest.raises(ValueError, match="duplicated in checkout"):
        check_reference_input_boundary(dataset.parent)


def test_shared_preflight_requires_declared_inputs_even_without_freshness_rules(inputs):
    from harness.web_fetch.reference_lifecycle import preflight_reference_checks

    dataset, manifest_path, source, _ = inputs
    cases = json.loads(dataset.read_text())["cases"]
    with pytest.raises(FileNotFoundError, match="not installed"):
        preflight_reference_checks(cases, dataset)
    import_inputs(manifest_path, source)
    assert preflight_reference_checks(cases, dataset) is None


def test_headless_runner_rejects_missing_inputs_before_provider_or_output(
    inputs, tmp_path, monkeypatch
):
    import sys

    from harness.web_fetch import headless_runner as run_headless_benchmark

    dataset, _, _, _ = inputs
    output = tmp_path / "headless-run"
    monkeypatch.setattr(run_headless_benchmark, "load_runtime_env", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing inputs must be rejected before provider construction")

    monkeypatch.setattr(run_headless_benchmark, "build_provider", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--dataset",
            str(dataset),
            "--output",
            str(output),
            "--providers",
            "fixture",
        ],
    )
    with pytest.raises(FileNotFoundError, match="not installed"):
        run_headless_benchmark.main()
    assert not output.exists()
