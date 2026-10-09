"""Offline diagnostics preserve their input and output identities."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.trajectory_replay import diagnostics as d
from harness.web_fetch.safety import unlinked_path

ROOT = Path(__file__).resolve().parents[2]


def readiness_files(tmp_path):
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            [
                {"index": 1, "result": "button disabled: Submit"},
                {
                    "index": 2,
                    "status": "success",
                    "arguments": {"action": "click", "selector": 'button:has-text("Submit")'},
                },
            ]
        )
    )
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "participant_id": "custom",
                        "task_id": "task-1",
                        "status": "pass",
                        "evidence": {
                            "trace": {
                                "path": str(trace),
                                "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                            }
                        },
                    }
                ]
            }
        )
    )
    return results, trace


def command(name, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), *map(str, args)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_readiness_command_creates_new_diagnostic_without_changing_a_pass(tmp_path):
    results, trace = readiness_files(tmp_path)
    originals = {path: path.read_bytes() for path in (results, trace)}
    output = tmp_path / "diagnostic.json"
    result = command("audit_replay_readiness.py", "--results", results, "--output", output)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_bytes())
    assert payload["score_effect"] == "none"
    assert payload["findings"][0]["historical_status"] == "pass"
    assert payload["findings"][0]["dispatch_state_known"] is False
    assert all(path.read_bytes() == content for path, content in originals.items())
    output.write_bytes(b"Independent receipt")
    result = command("audit_replay_readiness.py", "--results", results, "--output", output)
    assert result.returncode != 0 and output.read_bytes() == b"Independent receipt"


@pytest.mark.parametrize("boundary", ["alias", "linked-output", "linked-parent", "linked-input"])
def test_readiness_command_rejects_unsafe_boundaries(tmp_path, boundary):
    results, trace = readiness_files(tmp_path)
    original = results.read_bytes()
    output = tmp_path / "diagnostic.json"
    input_path = results
    if boundary == "alias":
        output = results
    elif boundary == "linked-output":
        output.symlink_to(results)
    elif boundary == "linked-input":
        input_path = tmp_path / "linked-results.json"
        input_path.symlink_to(results)
    else:
        linked = tmp_path / "linked"
        linked.symlink_to(tmp_path, target_is_directory=True)
        output = linked / "diagnostic.json"
    result = command("audit_replay_readiness.py", "--results", input_path, "--output", output)
    assert result.returncode != 0
    assert results.read_bytes() == original
    if boundary == "linked-output":
        assert output.is_symlink() and output.readlink() == results
    else:
        assert not (tmp_path / "diagnostic.json").exists()


def test_readiness_rejects_trace_changed_during_inspection(tmp_path, monkeypatch):
    results, trace = readiness_files(tmp_path)
    original = d.snapshot_readiness_gaps

    def change(trace_rows):
        value = original(trace_rows)
        trace.write_text("[]")
        return value

    monkeypatch.setattr(d, "snapshot_readiness_gaps", change)
    with pytest.raises(ValueError, match="input changed"):
        d.audit_readiness(results)


@pytest.mark.parametrize("link_kind", ["file", "directory", "dangling"])
def test_shared_path_guard_rejects_user_links(tmp_path, link_kind):
    target = tmp_path / "target"
    linked = tmp_path / "linked"
    if link_kind == "directory":
        target.mkdir()
        linked.symlink_to(target, target_is_directory=True)
        path = linked / "output.json"
    else:
        if link_kind == "file":
            target.write_text("retained")
        linked.symlink_to(target)
        path = linked
    with pytest.raises(ValueError, match="symlinked"):
        unlinked_path(path)


def test_shared_path_guard_supports_standard_temporary_directory():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "receipt.json"
        d.write_diagnostic(output, {"scope": "synthetic"})
        assert json.loads(output.read_bytes()) == {"scope": "synthetic"}


def test_shared_path_guard_checks_links_before_collapsing_parent_segments(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinked"):
        unlinked_path(linked / ".." / "output.json")
    assert unlinked_path(target / ".." / "output.json") == tmp_path / "output.json"


def test_cart_controls_execute_both_assertions_without_network(tmp_path):
    from harness.reporting.visual import browser_executable

    try:
        browser = Path(browser_executable())
    except ValueError as exc:
        pytest.skip(str(exc))
    dataset, candidate = tmp_path / "dataset.json", tmp_path / "candidate.json"
    frozen = [{"action": "evaluate", "script": "() => true"}]
    dataset.write_text(json.dumps({"trajectories": [{"task_id": 42, "tool_calls": frozen}]}))
    assertion = """() => {
      const rows = document.querySelectorAll('#shopping-cart-table tbody.cart.item');
      const fields = document.querySelectorAll('input[name="cart[1][qty]"]');
      const product = document.querySelector('.product-item-name');
      if (rows.length !== 1 || fields.length !== 1 || fields[0].value !== '1'
          || product.textContent !== 'Synthetic product') throw Error('cart mismatch');
      return true;
    }"""
    candidate.write_text(
        json.dumps(
            {
                "task_id": 42,
                "tool_calls": [{"action": "evaluate", "script": assertion}],
                "repair": {
                    "changed_step": 1,
                    "predecessor_dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                },
            }
        )
    )
    before = {path: path.read_bytes() for path in (dataset, candidate)}
    result = d.audit_cart_assertion(dataset, candidate, browser, product="Synthetic product")
    assert len(result["observations"]) == 9
    assert sum(row["candidate"]["passed"] for row in result["observations"]) == 1
    assert all(row["frozen"]["passed"] for row in result["observations"])
    assert result["network_allowed"] is False
    assert result["scope"] == "isolated_dom_not_task_scores"
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("kind", ["readiness", "cart", "recording", "exclusions"])
def test_occupied_outputs_stop_before_optional_execution(tmp_path, kind):
    from harness.trajectory_replay.public_webarena import diagnostics as public

    output = tmp_path / "retained.json"
    output.write_bytes(b"Independent receipt")
    missing = str(tmp_path / "missing")
    if kind == "readiness":
        entry, args = d.main_readiness, ["--results", missing]
    elif kind == "cart":
        entry, args = (
            d.main_cart_assertion,
            ["--dataset", missing, "--candidate", missing, "--browser", missing, "--product", "p"],
        )
    elif kind == "recording":
        entry, args = (
            public.main_recorded_outcomes,
            ["--catalog", missing, "--suite", missing, "--trace-dir", missing],
        )
    else:
        entry, args = (
            public.main_evaluator_exclusions,
            ["--suite", missing, "--exclusions", missing, "--trace-dir", missing],
        )
    with pytest.raises(ValueError, match="output already exists"):
        entry([*args, "--output", str(output)])
    assert output.read_bytes() == b"Independent receipt"
