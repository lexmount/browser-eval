"""Reference-input boundaries independent of any third-party historical dataset."""

import hashlib
import json

import pytest

from harness.web_fetch.reference_inputs import (
    import_inputs,
    input_tree,
    load_manifest,
    main,
    require_mutable_inputs,
)
from harness.web_fetch.storage import require_canonical_inputs


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    dataset = checkout / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tool": "web_fetch",
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
    (source / "sources/case-a/source.html").write_bytes(
        b"<main>independent fixture</main>"
    )
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
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    ) == ["sources/" + name for name in sorted(manifest["trees"]["sources"])]
    assert (
        output / "sources/case-a/reference.txt"
    ).read_bytes() == b"independent fixture"
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
def test_installed_inventory_is_exact_and_rejects_missing_extra_or_linked_files(
    inputs, damage
):
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
def test_manifest_rejects_wrong_dataset_escaping_paths_and_invalid_bindings(
    inputs, damage
):
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


def test_canonical_validation_compares_evidence_against_external_inputs(
    inputs, tmp_path
):
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
def test_reference_update_commands_reject_pinned_inputs_before_writes(
    inputs, tmp_path, owner
):
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
    from scripts import run_benchmark

    dataset, _, _, _ = inputs
    monkeypatch.setattr(run_benchmark, "load_runtime_env", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail(
            "Missing bound inputs must be rejected before provider construction or calls"
        )

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

    from scripts import run_headless_benchmark

    dataset, _, _, _ = inputs
    output = tmp_path / "headless-run"
    monkeypatch.setattr(run_headless_benchmark, "load_runtime_env", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing inputs must be rejected before provider construction")

    monkeypatch.setattr(run_headless_benchmark, "build_provider", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "runner", "--dataset", str(dataset), "--output", str(output),
        "--providers", "fixture",
    ])
    with pytest.raises(FileNotFoundError, match="not installed"):
        run_headless_benchmark.main()
    assert not output.exists()
