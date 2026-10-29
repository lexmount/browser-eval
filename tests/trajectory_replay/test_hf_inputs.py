"""Public replay definitions preserve action contracts without deployment evidence."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from webarena_verified.types import WebArenaSite

from harness.trajectory_replay.catalog_binding import (
    current_catalog_inputs,
    rebind_catalog,
)
from harness.trajectory_replay.catalog_contracts import file_sha256, load_catalog_inputs
from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.report_inputs import bound_dataset_path, replay_input_path
from harness.trajectory_replay.webarena_replay import _json_sha256


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def inputs(tmp_path):
    ports = {"shopping": 7770, "shopping_admin": 7780, "reddit": 9999, "gitlab": 8023}
    mapping = {
        name: f"http://public.example:{port}" + ("/admin" if name == "shopping_admin" else "")
        for name, port in ports.items()
    }
    sites_file = dump(
        tmp_path / "sites.json", {"schema": "lex_webarena_remote_sites/1", "sites": mapping}
    )
    sites = load_remote_sites(sites_file)
    source_sites = {
        WebArenaSite(k): value.replace("public.example", "private.invalid")
        for k, value in mapping.items()
    }
    calls = [
        {"action": "navigate", "url": "http://private.invalid:9999/", "save_as": "page"},
        {
            "action": "click",
            "selector": 'a[href="https://private.invalid:9999/item?q=1#section"]',
            "replay_mode": "snapshot_ref_semantics",
            "save_as": "clicked",
        },
        {
            "action": "evaluate",
            "script": 'return {"answer": ["entry"], "complete": true}',
            "save_as": "answer",
        },
    ]
    task = {
        "id": "webarena-1",
        "title": "Read the entry",
        "difficulty": "simple",
        "tags": [],
        "trajectory": {"schema": "lex_browser_trajectory/1", "actions": calls},
        "assertions": [],
        "evaluator": {"kind": "webarena_verified", "task_id": 1, "revision": 1},
        "environment": {
            "kind": "webarena_verified",
            "sites": ["reddit"],
            "start_urls": ["http://private.invalid:9999/"],
        },
        "ground_truth": {
            "oracle": "webarena_verified",
            "expected_actions": [c["action"] for c in calls],
        },
        "source": {
            "url": "https://github.com/web-arena-x/webarena",
            "verified_evaluators": [{"expected": {"response_cookies": {"mode": "required"}}}],
        },
    }
    suite = dump(
        tmp_path / "source-suite.json", {"schema": "lex_browser_use_tasks/4", "tasks": [task]}
    )
    from harness.trajectory_replay.retrieval_acceptance import digest

    contract = {
        "schema": "lex_replay_current_run_retrieval/1",
        "task_id": 2,
        "canonical_calls_sha256": digest(calls),
        "sources": [
            {"name": "observation", "step": 3, "action_sha256": digest(calls[2]), "decode": "json"}
        ],
        "projection": {"source": "observation", "pointer": "/answer"},
    }
    rows = [
        {
            "task_id": 1,
            "mode": "portable",
            "source_kind": "qualified_trace",
            "source_path": "/private/tmp/run/task.json",
        },
        {
            "task_id": 2,
            "mode": "direct",
            "source_kind": "qualified_direct",
            "source_actions": calls,
            "readiness": {"required": True},
            "retrieval_contract": contract,
        },
    ]
    for row in rows:
        row["source_site_urls"] = {k.value: v for k, v in source_sites.items()}
        row["bound_calls_sha256"] = _json_sha256(
            rebind_remote_calls(calls, sites, set(), source_sites)
        )
    catalog = dump(
        tmp_path / "source-catalog.json",
        {
            "schema": "lex_webarena_replay_catalog/1",
            "task_ids": [1, 2],
            "portable_suite_sha256": file_sha256(suite),
            "rows": rows,
        },
    )
    return catalog, suite, sites_file, calls, source_sites


def bound_inputs(tmp_path):
    catalog, suite, sites, calls, source_sites = inputs(tmp_path)
    projected = rebind_catalog(catalog, suite, sites)
    public_catalog = dump(tmp_path / "rebound-catalog.json", projected)
    return public_catalog, suite, sites, {}, calls, source_sites


def test_lazy_current_defaults_and_explicit_historical_paths(tmp_path):
    from harness.trajectory_replay.public_webarena.suite import load_suite as human_suite
    from harness.trajectory_replay.suite import load_suite as curated_suite

    original = replay_input_path(current="curated")
    _, public_suite, _, _, _, _ = bound_inputs(tmp_path)

    def resolve(track, relative, revision=None):
        assert revision is None
        return original if track == "trajectory-replay" else public_suite

    with patch("harness.benchmarks.data.definition_path", side_effect=resolve) as remote:
        assert len(curated_suite().tasks) == 30
        assert len(human_suite().tasks) == 1
        assert replay_input_path(original, current="curated") == original
        assert len(curated_suite(original).tasks) == 30
        assert remote.call_count == 2
        descriptor = {
            "benchmark_dataset": "webarena-replay",
            "definition": "canonical/corrected-90-v1.json",
            "sha256": file_sha256(public_suite),
        }
        assert bound_dataset_path({"dataset": descriptor}) == public_suite
    # Explicit old bundles do not invoke HF resolution.
    with patch("harness.benchmarks.data.definition_path", side_effect=AssertionError("network")):
        assert (
            bound_dataset_path(
                {"dataset": {"path": str(public_suite), "sha256": file_sha256(public_suite)}}
            )
            == public_suite
        )


def test_current_catalog_rebinds_once_and_rejects_changed_resume_inputs(tmp_path):
    catalog, suite, sites_path, _, _, _ = bound_inputs(tmp_path)
    source_paths = {"catalog/full-812-v1.json": catalog, "suites/full-portable-v1.json": suite}

    def resolve(track, relative, revision=None):
        return source_paths[relative]

    target = json.loads(sites_path.read_text())
    target["sites"] = {
        k: v.replace("public.example", "deployment.invalid") for k, v in target["sites"].items()
    }
    deployment = dump(tmp_path / "deployment.json", target)
    binding = tmp_path / "run/input-catalog.json"
    with patch("harness.benchmarks.data.definition_path", side_effect=resolve):
        selected, portable = current_catalog_inputs(deployment, binding)
        assert selected == binding and portable == suite
        assert load_catalog_inputs(selected, portable, deployment)[0]["task_ids"] == [1, 2]
        assert current_catalog_inputs(deployment, binding) == (selected, portable)
        payload = json.loads(catalog.read_text())
        payload["rows"][0]["source_kind"] = "changed"
        dump(catalog, payload)
        with pytest.raises(ValueError, match="different catalog input binding"):
            current_catalog_inputs(deployment, binding)


def test_report_rebuild_selects_exact_locked_suite_hash(tmp_path):
    from harness.trajectory_replay.report_inputs import replay_suite_for_hash

    files = {}
    for name in (
        "suites/human-qualified-v1.json",
        "inventories/human-candidates-v1.json",
        "historical/normalized-example-v1.json",
    ):
        files[name] = dump(tmp_path / Path(name).name, {"source": name})

    def resolve(track, relative, revision=None):
        return files[relative]

    with patch("harness.benchmarks.data.definition_path", side_effect=resolve):
        original = files["inventories/human-candidates-v1.json"]
        assert replay_suite_for_hash(file_sha256(original), family="human") == original
        with pytest.raises(ValueError, match="explicit path"):
            replay_suite_for_hash("0" * 64, family="human")


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("no_report", [False, True])
def test_replay_cli_report_option(public, no_report, tmp_path):
    import sys

    from harness.trajectory_replay import cli as curated_cli
    from harness.trajectory_replay.public_webarena import cli as human_cli

    cli = human_cli if public else curated_cli
    argv = [
        "replay",
        "--suite",
        str(tmp_path / "explicit.json"),
        "--output",
        str(tmp_path / "run"),
        "--ground-truth-evidence",
        str(tmp_path / "qualification.json"),
    ]
    if no_report:
        argv.append("--no-report")
    with patch.object(sys, "argv", argv), patch.object(cli, "run_evaluation") as run:
        if public:
            with (
                patch.object(cli, "load_runtime_env"),
                patch.object(cli, "configure_browser_docker_host"),
            ):
                cli.main()
        else:
            cli.main()
    assert run.call_args.kwargs["generate_report"] is not no_report


@pytest.mark.parametrize(
    "flags",
    [
        ["--action-timeout-ms", "0"],
        ["--task-timeout-ms", "0"],
        [],
        ["--engine", "kitesurf"],
        ["--engine", "kitesurf", "--binary", "browser"],
    ],
)
def test_invalid_catalog_invocation_never_materializes_default_inputs(tmp_path, monkeypatch, flags):
    from harness.trajectory_replay import catalog_runner

    output = tmp_path / "run"
    monkeypatch.setattr(catalog_runner, "verify_webarena_runtime_source", lambda: None)
    monkeypatch.setattr(
        "harness.trajectory_replay.catalog_binding.current_catalog_inputs",
        lambda *a, **k: pytest.fail("inputs materialized before argument validation"),
    )
    argv = [
        "catalog",
        "--site-map",
        "sites",
        "--site-reset-receipt",
        "reset",
        "--environment-profile",
        "environment",
        "--output",
        str(output),
        *flags,
    ]
    with patch("sys.argv", argv), pytest.raises(ValueError):
        catalog_runner.main()
    assert not output.exists()


@pytest.mark.parametrize("selection", [None, 999])
def test_default_catalog_output_is_private_and_only_created_after_validation(
    tmp_path, monkeypatch, selection
):
    import os
    from types import SimpleNamespace

    from harness.trajectory_replay import catalog_runner

    catalog, suite, sites, _, _, _ = bound_inputs(tmp_path)
    source_paths = {"catalog/full-812-v1.json": catalog, "suites/full-portable-v1.json": suite}
    environment = dump(tmp_path / "environment.json", {})
    binary = tmp_path / "browser"
    binary.write_bytes(b"test binary")
    output = tmp_path / "run"
    monkeypatch.setattr(
        catalog_runner, "verify_webarena_runtime_source", lambda: SimpleNamespace(revision="a" * 40)
    )
    monkeypatch.setattr(catalog_runner, "validate_environment_preparation", lambda *a: None)
    monkeypatch.setattr(
        catalog_runner, "load_corrections", lambda: SimpleNamespace(source_sha256="b" * 64)
    )
    monkeypatch.setattr(catalog_runner, "remote_benchmark", lambda *a: None)
    monkeypatch.setattr(catalog_runner, "_recover_completed_attempts", lambda *a: {1, 2})
    argv = [
        "catalog",
        "--site-map",
        str(sites),
        "--site-reset-receipt",
        str(environment),
        "--environment-profile",
        str(environment),
        "--output",
        str(output),
        "--binary",
        str(binary),
        "--backend",
        "playwright",
    ]
    if selection is not None:
        argv.extend(["--task-id", str(selection)])
    prior = os.umask(0o022)
    try:
        with (
            patch("sys.argv", argv),
            patch(
                "harness.benchmarks.data.definition_path",
                side_effect=lambda track, relative, revision=None: source_paths[relative],
            ),
        ):
            if selection is None:
                catalog_runner.main()
                assert output.stat().st_mode & 0o777 == 0o700
                assert (output / "input-catalog.json").stat().st_mode & 0o777 == 0o600
            else:
                with pytest.raises(ValueError, match="catalog members"):
                    catalog_runner.main()
                assert not output.exists()
    finally:
        os.umask(prior)


@pytest.mark.parametrize("changed", [False, True])
def test_current_catalog_requires_frozen_suite_before_signing(tmp_path, changed):
    catalog, suite, sites, _, _, _ = bound_inputs(tmp_path)
    copy = tmp_path / "supplied-suite.json"
    payload = json.loads(suite.read_bytes())
    if changed:
        payload["tasks"][0]["trajectory"]["actions"][0]["url"] += "unqualified"
        dump(copy, payload)
    else:
        copy.write_bytes(suite.read_bytes())
    binding = tmp_path / "run/input-catalog.json"
    with patch("harness.benchmarks.data.definition_path", return_value=catalog):
        if changed:
            with pytest.raises(ValueError, match="frozen portable suite"):
                current_catalog_inputs(sites, binding, suite_path=copy)
            assert not binding.parent.exists()
        else:
            selected, portable = current_catalog_inputs(sites, binding, suite_path=copy)
            assert portable == copy
            assert load_catalog_inputs(selected, portable, sites)[0]["task_ids"] == [1, 2]


def test_catalog_mapping_reaches_retrieval_consumption_after_deployment_rebind(tmp_path):
    from harness.trajectory_replay.catalog_runner import _bound_catalog_row
    from harness.trajectory_replay.remote_execution import _read_retrieval_contract

    catalog, suite, sites, _, _ = inputs(tmp_path)
    source = json.loads(catalog.read_bytes())
    source["source_site_urls"] = source["rows"][0]["source_site_urls"]
    for row in source["rows"]:
        row.pop("source_site_urls")
    dump(catalog, source)
    rebound = dump(tmp_path / "rebound.json", rebind_catalog(catalog, suite, sites))
    loaded, deployment, tasks = load_catalog_inputs(rebound, suite, sites)
    row = _bound_catalog_row(loaded, loaded["rows"][1], tasks, deployment)
    assert row["source_site_urls"] == source["source_site_urls"]
    candidate = _read_retrieval_contract(2, row, deployment)
    assert candidate["tool_calls"] == source["rows"][1]["source_actions"]


@pytest.mark.parametrize("observed", [False, True])
def test_runtime_retrieval_preserves_preparation_but_not_qualified_observation(tmp_path, observed):
    from harness.trajectory_replay.catalog_contracts import ACTION_OVERRIDES_SCHEMA
    from harness.trajectory_replay.catalog_runner import _bound_catalog_row
    from harness.trajectory_replay.remote_execution import _read_retrieval_contract

    catalog, _, sites, calls, _ = inputs(tmp_path)
    source = json.loads(catalog.read_bytes())
    index = 3 if observed else 2
    changes = [
        {
            "task_id": 2,
            "action_index": index,
            "precondition_action_sha256": _json_sha256(calls[index - 1]),
            "set": {"script": "return 999"} if observed else {"force": True},
        }
    ]
    source["action_overrides_sha256"] = "a" * 64
    source["rows"][1]["action_overrides"] = {
        "schema": ACTION_OVERRIDES_SCHEMA,
        "source_sha256": "a" * 64,
        "amendments_sha256": _json_sha256(changes),
        "amendments": changes,
    }
    deployment = load_remote_sites(sites)
    row = _bound_catalog_row(source, source["rows"][1], {}, deployment)
    row["bound_calls_sha256"] = _json_sha256(row["actions"])
    if observed:
        with pytest.raises(ValueError, match="qualified observation action changed"):
            _read_retrieval_contract(2, row, deployment)
    else:
        assert _read_retrieval_contract(2, row, deployment)["tool_calls"] == calls


def test_default_catalog_explicit_overrides_are_rejected_before_loading(tmp_path, monkeypatch):
    from harness.trajectory_replay import catalog_runner

    argv = [
        "catalog",
        "--site-map",
        "sites",
        "--site-reset-receipt",
        "reset",
        "--environment-profile",
        "environment",
        "--output",
        str(tmp_path / "run"),
        "--action-overrides",
        "overrides.json",
    ]
    with patch("sys.argv", argv), pytest.raises(ValueError, match="explicit qualified --catalog"):
        catalog_runner.main()
    assert not (tmp_path / "run").exists()
