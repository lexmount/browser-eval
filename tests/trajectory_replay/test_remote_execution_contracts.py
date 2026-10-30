"""Controls for the diagnostic original-failure cohort runner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.catalog_runner import _chromium_host_resolver_rules
from harness.trajectory_replay.remote_execution import (
    _browser_command,
    _read_retrieval_contract,
    _task_type,
    _verify_result_evidence,
    _wait_cdp,
)
from harness.trajectory_replay.remote_runtime import rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256


def _sites() -> dict[WebArenaSite, str]:
    return {
        WebArenaSite.SHOPPING: "https://shopping.example.test",
        WebArenaSite.SHOPPING_ADMIN: "https://admin.example.test/admin",
        WebArenaSite.REDDIT: "https://reddit.example.test",
        WebArenaSite.GITLAB: "https://gitlab.example.test",
    }


def _retrieval_contract(task_id, calls):
    return {
        "schema": "lex_replay_current_run_retrieval/1",
        "task_id": task_id,
        "canonical_calls_sha256": _json_sha256(calls),
        "sources": [
            {
                "name": "observed",
                "step": len(calls),
                "action_sha256": _json_sha256(calls[-1]),
                "decode": "json",
            }
        ],
        "projection": {"source": "observed", "pointer": "/value"},
    }



def test_retrieval_contract_is_bound_to_executed_remote_calls() -> None:
    task_id = 108
    candidate = json.loads(
        (
            Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
            / f"task-{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    sites = _sites()
    rebound = rebind_remote_calls(
        candidate["tool_calls"],
        sites,
        {"127.0.0.1", "192.168.5.2", "host.docker.internal"},
    )
    row = {"bound_calls_sha256": _json_sha256(rebound)}
    assert _read_retrieval_contract(task_id, row, sites) == candidate
    with pytest.raises(ValueError, match="differ from frozen bound calls"):
        _read_retrieval_contract(task_id, {"bound_calls_sha256": "0" * 64}, sites)


def test_retrieval_contract_uses_the_qualified_source_revision() -> None:
    task_id = 108
    path = Path(
        "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/"
        "task-108-ajax-proposal.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    sites = _sites()
    rebound = rebind_remote_calls(
        candidate["tool_calls"],
        sites,
        {"127.0.0.1", "192.168.5.2", "host.docker.internal"},
    )
    row = {
        "bound_calls_sha256": _json_sha256(rebound),
        "source_revision": {"path": str(path)},
    }
    assert _read_retrieval_contract(task_id, row, sites) == candidate


def test_retrieval_contract_can_be_frozen_inside_a_catalog() -> None:
    task_id = 108
    candidate = json.loads(
        (
            Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
            / f"task-{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    sites = _sites()
    rebound = rebind_remote_calls(
        candidate["tool_calls"],
        sites,
        {"127.0.0.1", "192.168.5.2", "host.docker.internal"},
    )
    row = {
        "source_actions": candidate["tool_calls"],
        "retrieval_contract": candidate["retrieval_contract"],
        "bound_calls_sha256": _json_sha256(rebound),
    }
    assert _read_retrieval_contract(task_id, row, sites) == candidate
    row["retrieval_contract"] = {**row["retrieval_contract"], "task_id": 109}
    with pytest.raises(ValueError, match="identity mismatch"):
        _read_retrieval_contract(task_id, row, sites)


def test_embedded_retrieval_contract_validates_effective_calls_after_action_override() -> None:
    task_id = 28
    sites = _sites()
    source_action = {
        "action": "evaluate",
        "script": "const value = 1; return value;",
    }
    observation = {"action": "evaluate", "script": 'return {"value": 1};'}
    source_calls = [source_action, observation]
    contract = _retrieval_contract(task_id, source_calls)
    amendment = {
        "task_id": task_id,
        "action_index": 1,
        "precondition_action_sha256": _json_sha256(source_action),
        "set": {"script_mode": "function_body"},
    }
    effective_calls = [{**source_action, "script_mode": "function_body"}, observation]
    overrides_sha256 = "a" * 64
    row = {
        "source_actions": source_calls,
        "retrieval_contract": contract,
        "bound_calls_sha256": _json_sha256(effective_calls),
        "action_overrides": {
            "schema": "lex_webarena_replay_action_overrides/1",
            "source_sha256": overrides_sha256,
            "amendments_sha256": _json_sha256([amendment]),
            "amendments": [amendment],
        },
    }

    candidate = _read_retrieval_contract(task_id, row, sites)

    assert candidate["tool_calls"] == source_calls
    assert candidate["retrieval_contract"]["canonical_calls_sha256"] == _json_sha256(source_calls)


def test_embedded_retrieval_contract_rejects_stale_override_precondition() -> None:
    task_id = 28
    action = {"action": "evaluate", "script": "return true;"}
    amendment = {
        "task_id": task_id,
        "action_index": 1,
        "precondition_action_sha256": "0" * 64,
        "set": {"script_mode": "function_body"},
    }
    row = {
        "source_actions": [action],
        "retrieval_contract": {"task_id": task_id},
        "bound_calls_sha256": "0" * 64,
        "action_overrides": {
            "schema": "lex_webarena_replay_action_overrides/1",
            "source_sha256": "a" * 64,
            "amendments_sha256": _json_sha256([amendment]),
            "amendments": [amendment],
        },
    }

    with pytest.raises(ValueError, match="precondition changed"):
        _read_retrieval_contract(task_id, row, _sites())


def test_embedded_retrieval_contract_rebinds_its_recorded_deployment() -> None:
    task_id = 243
    sites = _sites()
    source_sites = {WebArenaSite.GITLAB: "https://old-gitlab.example.test"}
    calls = [{"action": "navigate", "url": "https://old-gitlab.example.test/owner/project"}]
    calls.append({"action": "evaluate", "script": 'return {"value": 1};'})
    contract = _retrieval_contract(task_id, calls)
    rebound = rebind_remote_calls(calls, sites, set(), source_sites)
    row = {
        "source_actions": calls,
        "source_site_urls": {site.value: url for site, url in source_sites.items()},
        "retrieval_contract": contract,
        "bound_calls_sha256": _json_sha256(rebound),
    }
    assert rebound[0]["url"] == "https://gitlab.example.test/owner/project"
    assert _read_retrieval_contract(task_id, row, sites)["tool_calls"] == calls
    row["source_site_urls"] = {"gitlab": "https://different-gitlab.example.test"}
    with pytest.raises(ValueError, match="differ from frozen bound calls"):
        _read_retrieval_contract(task_id, row, sites)


def test_response_type_comes_from_task_contract() -> None:
    expected = SimpleNamespace(model_dump=lambda **kwargs: {"task_type": "MUTATE"})
    task = SimpleNamespace(
        task_id=1,
        eval=[SimpleNamespace(evaluator="AgentResponseEvaluator", expected=expected)],
    )
    assert _task_type(task) == "MUTATE"
    task.eval[0].expected = SimpleNamespace(model_dump=lambda **kwargs: {"task_type": "RETRIEVE"})
    with pytest.raises(ValueError, match="response type unavailable"):
        _task_type(task)


def test_chromium_qualification_uses_a_separate_ephemeral_profile() -> None:
    chrome = _browser_command(
        SimpleNamespace(engine="chromium", binary=Path("/opt/chrome")),
        9222,
        "/tmp/qualification-profile",
    )
    moli = _browser_command(
        SimpleNamespace(engine="moli", binary=Path("/opt/moli")),
        9222,
        "/tmp/qualification-profile",
    )
    assert chrome[0] == "/opt/chrome"
    assert "--headless=new" in chrome
    expected_disabled = "HttpsUpgrades,HttpsFirstBalancedMode,HttpsFirstModeIncognito"
    assert f"--disable-features={expected_disabled}" in chrome
    assert not any("disable-web-security" in arg for arg in chrome)
    assert "--user-data-dir=/tmp/qualification-profile" in chrome
    assert moli[0:2] == ["/opt/moli", "serve"]
    assert moli[-2:] == ["--profile-dir", "/tmp/qualification-profile"]


def test_comparison_browsers_use_their_native_cdp_serve_contracts() -> None:
    lightpanda = _browser_command(
        SimpleNamespace(engine="lightpanda", binary=Path("/opt/lightpanda")),
        9222,
        "/tmp/qualification-profile",
    )
    obscura = _browser_command(
        SimpleNamespace(engine="obscura", binary=Path("/opt/obscura")),
        9222,
        "/tmp/qualification-profile",
    )
    assert lightpanda == [
        "/opt/lightpanda",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "9222",
    ]
    assert obscura == [
        "/opt/obscura",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "9222",
        "--storage-dir",
        "/tmp/qualification-profile",
        "--allow-private-network",
    ]


def test_chromium_can_bind_profile_declared_private_ingress() -> None:
    rules = _chromium_host_resolver_rules(
        {
            "host_overrides": [
                {"pattern": "*.dev.lexmount.net", "address": "10.2.62.142"}
            ]
        },
        "chromium",
    )
    chrome = _browser_command(
        SimpleNamespace(
            engine="chromium",
            binary=Path("/opt/chrome"),
            host_resolver_rules=rules,
        ),
        9222,
        "/tmp/qualification-profile",
    )
    assert "--host-resolver-rules=MAP *.dev.lexmount.net 10.2.62.142" in chrome


def test_environment_host_overrides_are_not_compiled_for_other_engines() -> None:
    profile = {
        "host_overrides": [
            {"pattern": "*.dev.lexmount.net", "address": "10.2.62.142"}
        ]
    }
    assert _chromium_host_resolver_rules(profile, "moli") is None


def test_chromium_still_reads_legacy_host_resolver_profile() -> None:
    profile = {"browser_host_resolver_rules": "MAP legacy.example 127.0.0.1"}
    assert (
        _chromium_host_resolver_rules(profile, "chromium")
        == "MAP legacy.example 127.0.0.1"
    )


def test_formal_evidence_boundary_rejects_missing_or_changed_files(tmp_path: Path) -> None:
    import hashlib

    directory = tmp_path / "task-29"
    directory.mkdir()
    trace = directory / "trace.json"
    trace.write_text("[]\n", encoding="utf-8")
    digest = hashlib.sha256(trace.read_bytes()).hexdigest()
    rows = [{"task_id": 29, "evidence_sha256": {"trace": digest}}]
    _verify_result_evidence(tmp_path, rows)
    trace.write_text("[{}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="evidence hash changed"):
        _verify_result_evidence(tmp_path, rows)
    trace.unlink()
    trace.symlink_to(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="evidence hash changed"):
        _verify_result_evidence(tmp_path, rows)


def test_wait_cdp_pauses_between_non_ready_responses(monkeypatch) -> None:
    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    sleeps: list[float] = []
    monkeypatch.setattr(
        "harness.trajectory_replay.remote_execution.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "harness.trajectory_replay.remote_execution.time.sleep", sleeps.append
    )

    with pytest.raises(TimeoutError, match="CDP did not become ready"):
        _wait_cdp("http://127.0.0.1:9", SimpleNamespace(poll=lambda: None))

    assert sleeps == [0.1] * 300
