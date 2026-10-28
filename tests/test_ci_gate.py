"""The existing required CI status must include every independent check."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml


def ci_gate():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
    )
    return workflow["jobs"]["test"]


def run_gate(results):
    step = ci_gate()["steps"][0]
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**os.environ, "CHECK_RESULTS": " ".join(results)},
        check=False,
    ).returncode


def test_required_ci_status_accepts_all_checks_passing():
    gate = ci_gate()
    assert gate["name"] == "Python 3.11"
    assert run_gate(["success"] * len(gate["needs"])) == 0


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped"])
def test_required_ci_status_rejects_each_unsuccessful_check(outcome):
    for index in range(len(ci_gate()["needs"])):
        results = ["success"] * len(ci_gate()["needs"])
        results[index] = outcome
        assert run_gate(results) != 0


def test_required_ci_status_rejects_missing_results():
    assert run_gate([]) != 0
