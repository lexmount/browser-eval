"""Public replay definitions preserve action contracts without deployment evidence."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from webarena_verified.types import WebArenaSite

from harness.trajectory_replay.catalog_binding import (
    current_catalog_inputs,
    public_catalog_inputs,
    public_replay_calls,
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


def export(tmp_path):
    catalog, suite, sites, calls, source_sites = inputs(tmp_path)
    projected, tasks, receipts = public_catalog_inputs(
        catalog, suite, sites, catalog_sha256=file_sha256(catalog), suite_sha256=file_sha256(suite)
    )
    public_suite = dump(tmp_path / "public-suite.json", tasks)
    projected["portable_suite_sha256"] = file_sha256(public_suite)
    public_catalog = dump(tmp_path / "public-catalog.json", projected)
    return public_catalog, public_suite, sites, receipts, calls, source_sites


def test_public_inputs_keep_actions_rules_and_population_without_run_paths(tmp_path):
    catalog, suite, sites, receipt, calls, source_sites = export(tmp_path)
    loaded, bound_sites, tasks = load_catalog_inputs(catalog, suite, sites)
    assert loaded["task_ids"] == [1, 2] and set(tasks) == {1}
    text = catalog.read_text() + suite.read_text()
    assert "private.invalid" not in text and "/private/tmp/" not in text
    assert tasks[1].source["verified_evaluators"][0]["expected"]["response_cookies"] == {
        "mode": "required"
    }
    assert tasks[1].tool_calls[1]["replay_mode"] == "snapshot_ref_semantics"
    assert ":is(" not in tasks[1].tool_calls[1]["selector"]
    assert receipt["tasks"][0]["source_actions_sha256"] == _json_sha256(calls)
    assert (
        receipt["tasks"][1]["source_retrieval_contract_sha256"]
        != receipt["tasks"][1]["public_retrieval_contract_sha256"]
    )
    # This observation script is unchanged; only URL-bearing source actions change.
    assert loaded["rows"][1]["retrieval_contract"]["canonical_calls_sha256"] != _json_sha256(calls)
    assert loaded["rows"][1]["retrieval_contract"]["projection"] == {
        "source": "observation",
        "pointer": "/answer",
    }


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_public_origin_view_matches_original_under_explicit_deployment(tmp_path, scheme):
    _, _, sites_path, calls, source_sites = inputs(tmp_path)
    public_sites = load_remote_sites(sites_path)
    projected = public_replay_calls(calls, source_sites, public_sites)
    deployment = {
        key: value.replace("http://public.example", f"{scheme}://deployment.invalid")
        for key, value in public_sites.items()
    }
    assert rebind_remote_calls(
        projected, deployment, set(), public_sites, origins_only=True
    ) == rebind_remote_calls(calls, deployment, set(), source_sites, origins_only=True)
    assert rebind_remote_calls(projected, deployment, set(), public_sites) == rebind_remote_calls(
        calls, deployment, set(), source_sites
    )


def test_changed_authority_or_unportable_origin_is_rejected(tmp_path):
    catalog, suite, sites, _, _ = inputs(tmp_path)
    with pytest.raises(ValueError, match="authority changed"):
        public_catalog_inputs(
            catalog, suite, sites, catalog_sha256="0" * 64, suite_sha256=file_sha256(suite)
        )
    data = json.loads(suite.read_text())
    data["tasks"][0]["source"]["private_note"] = "http://private.invalid:9999/run"
    dump(suite, data)
    source = json.loads(catalog.read_text())
    source["portable_suite_sha256"] = file_sha256(suite)
    dump(catalog, source)
    with pytest.raises(ValueError, match="source deployment remains"):
        public_catalog_inputs(
            catalog,
            suite,
            sites,
            catalog_sha256=file_sha256(catalog),
            suite_sha256=file_sha256(suite),
        )


def test_lazy_current_defaults_and_explicit_historical_paths(tmp_path):
    from harness.trajectory_replay.public_webarena.suite import load_suite as human_suite
    from harness.trajectory_replay.suite import load_suite as curated_suite

    original = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/tasks.v4.json"
    _, public_suite, _, _, _, _ = export(tmp_path)

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
    catalog, suite, sites_path, _, _, _ = export(tmp_path)
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


@pytest.mark.parametrize("task_type", ["RETRIEVE", "MUTATE", "NAVIGATE"])
def test_corrected_public_inputs_exclude_prior_responses_without_losing_rules(tmp_path, task_type):
    from harness.trajectory_replay.catalog_binding import public_corrected_inputs

    _, _, sites_path, calls, source_sites = inputs(tmp_path)
    original = {
        "id": "webarena-7",
        "task_id": 7,
        "task": {
            "intent": "Read the entry",
            "sites": ["reddit"],
            "eval": [
                {
                    "expected": {
                        "retrieved_data": ["independent reference"],
                        "response_cookies": {"mode": "required"},
                    }
                }
            ],
        },
        "tool_calls": calls,
        "source": {"kind": "historical_seed", "response_sha256": "a" * 64},
        "evaluation_input": {
            "task_type": task_type,
            "status": "SUCCESS",
            "retrieved_data": ["PRIOR-OBSERVED-SECRET"],
            "response": "OLD-RESPONSE-BODY",
            "error_details": "old result",
        },
        "verification": {"status": "pass", "observation": "PRIOR-OBSERVED-SECRET"},
    }
    source = dump(
        tmp_path / "corrected.json",
        {
            "schema": "lex_webarena_verified_trajectory_dataset/1",
            "benchmark": "WebArena-Verified",
            "benchmark_version": "historical",
            "official_data_checksum": "b" * 64,
            "selection_sha256": "c" * 64,
            "semantics": {},
            "trajectories": [original],
        },
    )
    projected, receipt = public_corrected_inputs(
        source,
        load_remote_sites(sites_path),
        dataset_sha256=file_sha256(source),
        source_sites_by_task={7: source_sites},
    )
    public = projected["trajectories"][0]
    assert public["evaluation_input"] == {"task_type": task_type}
    assert public["task"] == original["task"]
    assert public["source"] == original["source"]
    assert public["tool_calls"] == public_replay_calls(
        calls, source_sites, load_remote_sites(sites_path)
    )
    text = json.dumps({"definition": projected, "receipt": receipt})
    assert "PRIOR-OBSERVED-SECRET" not in text and "OLD-RESPONSE-BODY" not in text
    assert "independent reference" in text and "response_cookies" in text
    assert receipt["tasks"][0]["historical_evaluation_input_sha256"] == _json_sha256(
        original["evaluation_input"]
    )
    assert receipt["tasks"][0]["historical_qualification_sha256"] == _json_sha256(
        original["verification"]
    )
    assert "observation_protocol" not in projected["provenance"]
    from harness.trajectory_replay.webarena_replay import validate_qualification_dataset

    path = dump(tmp_path / "public-corrected.json", projected)
    with patch("harness.trajectory_replay.webarena_replay.require_current_retrieval"):
        with pytest.raises(ValueError, match="fresh valid-observation qualification"):
            validate_qualification_dataset({}, path)
