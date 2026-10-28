"""The existing required CI status must include every independent check."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def ci_workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text()
    )


def ci_gate():
    return ci_workflow()["jobs"]["test"]


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


def concurrency_for(contexts):
    result = subprocess.run(
        ["node", str(ROOT / "tests/evaluate_ci_expressions.mjs")],
        input=json.dumps({"policy": ci_workflow()["concurrency"], "contexts": contexts}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def github_context(run_id, *, event="push", ref="refs/heads/main", pr=None, workflow="ci"):
    return {
        "workflow": workflow,
        "event_name": event,
        "run_id": run_id,
        "ref": ref,
        "event": {"pull_request": {"number": pr}} if pr is not None else {},
    }


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v0.3.0"])
def test_three_non_pr_pushes_cannot_replace_a_pending_run(ref):
    runs = concurrency_for([github_context(run_id, ref=ref) for run_id in (101, 102, 103)])
    assert len({run["group"] for run in runs}) == 3
    assert all(not run["cancel"] for run in runs)


def test_new_commits_replace_only_runs_of_the_same_pr():
    runs = concurrency_for([
        github_context(101, event="pull_request", pr=77, ref="refs/pull/77/merge"),
        github_context(102, event="pull_request", pr=77, ref="refs/pull/77/merge"),
        github_context(103, event="pull_request", pr=78, ref="refs/pull/78/merge"),
    ])
    assert runs[0]["group"] == runs[1]["group"]
    assert runs[0]["group"] != runs[2]["group"]
    assert all(run["cancel"] for run in runs)


def test_concurrency_does_not_cross_workflow_boundaries():
    runs = concurrency_for([
        github_context(101, event="pull_request", pr=77, workflow="ci"),
        github_context(102, event="pull_request", pr=77, workflow="other"),
    ])
    assert runs[0]["group"] != runs[1]["group"]


@pytest.mark.parametrize("job", ["checks", "package", "browser", "test"])
def test_each_ci_job_has_a_bounded_budget_including_setup(job):
    budget = ci_workflow()["jobs"][job].get("timeout-minutes")
    assert isinstance(budget, int) and 0 < budget <= 30


def test_ci_actions_are_bound_to_immutable_commits():
    for job in ci_workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[a-f0-9]{40}", step["uses"])
