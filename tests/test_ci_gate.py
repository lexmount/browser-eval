"""The existing required CI status must include every independent check."""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def ci_workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )


def ci_gate():
    return ci_workflow()["jobs"]["test"]


def run_gate(results, *, docs_only="false"):
    step = ci_gate()["steps"][0]
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**os.environ, "CHECK_RESULTS": " ".join(results), "DOCS_ONLY": docs_only},
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


def test_documentation_gate_accepts_only_the_declared_skips():
    assert run_gate(["success", "success", "skipped", "skipped"], docs_only="true") == 0
    for index in range(4):
        for outcome in ["success", "failure", "cancelled", "skipped"]:
            results = ["success", "success", "skipped", "skipped"]
            if results[index] != outcome:
                results[index] = outcome
                assert run_gate(results, docs_only="true") != 0


@pytest.mark.parametrize("mode", ["", "unknown", "True"])
def test_required_ci_status_rejects_an_unknown_scope(mode):
    assert run_gate(["success"] * 4, docs_only=mode) != 0


def git(root, *args, input=None):
    return subprocess.run(
        ["git", "-C", str(root), *args], input=input, text=True,
        capture_output=True, check=True,
    ).stdout.strip()


@pytest.fixture
def pr_checkout(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "CI test")
    git(tmp_path, "config", "user.email", "ci@example.test")

    def commit(changes):
        for relative, content in changes.items():
            path = tmp_path / relative
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "--allow-empty", "-m", "Test change")

    commit({"README.md": "# Example", "src/scorer.py": "", "docs/guide.md": "# Guide"})
    base = git(tmp_path, "rev-parse", "HEAD")

    def checkout(*commits):
        for changes in commits:
            commit(changes)
        head = git(tmp_path, "rev-parse", "HEAD")
        tree = git(tmp_path, "rev-parse", "HEAD^{tree}")
        merge = git(tmp_path, "commit-tree", tree, "-p", base, "-p", head, input="PR merge\n")
        git(tmp_path, "switch", "--detach", merge)
        return tmp_path

    return checkout


def check_scope(root, event="pull_request"):
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/ci_scope.sh")], cwd=root,
        env={**os.environ, "GITHUB_EVENT_NAME": event},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout in {"docs_only=true\n", "docs_only=false\n"}
    return result.stdout == "docs_only=true\n"


@pytest.mark.parametrize("path", [
    "README.md", "CONTRIBUTING.md", "AGENTS.md", "docs/read me.md",
    "docs/说明.md", "docs/line\nbreak.md", "docs/assets/banner.webp",
])
def test_documentation_prs_use_the_lightweight_checks(pr_checkout, path):
    assert check_scope(pr_checkout({path: "Updated documentation"}))


@pytest.mark.parametrize("path", [
    "src/harness/benchmarks/cards/webfetch.md", "src/scorer.py", "tests/test_scoring.py",
    "Makefile", "uv.lock", ".github/workflows/ci.yml", ".agents/skills/example/SKILL.md",
    "docs/assets/runtime.js", "docs/inputs.json", "THIRD_PARTY.md",
])
def test_code_data_and_unknown_paths_keep_the_full_checks(pr_checkout, path):
    assert not check_scope(pr_checkout({"README.md": "Updated", path: "Changed"}))


def test_earlier_code_changes_cannot_be_hidden_by_a_final_docs_commit(pr_checkout):
    root = pr_checkout({"src/scorer.py": "Changed"}, {"README.md": "Updated"})
    assert not check_scope(root)


def test_a_code_file_renamed_into_docs_keeps_the_full_checks(pr_checkout):
    root = pr_checkout({"src/scorer.py": None, "docs/scorer.md": ""})
    assert not check_scope(root)


@pytest.mark.parametrize("depth, docs_only", [(1, False), (2, True)])
def test_shallow_pr_checkout_requires_the_base_tree(pr_checkout, tmp_path, depth, docs_only):
    source = pr_checkout({"docs/guide.md": "Updated"}, {"README.md": "Updated"})
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--no-local", "--depth", str(depth), str(source), str(clone))
    assert git(clone, "rev-parse", "--is-shallow-repository") == "true"
    assert check_scope(clone) is docs_only


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", ""])
def test_non_pr_events_always_keep_the_full_checks(pr_checkout, event):
    assert not check_scope(pr_checkout({"README.md": "Updated"}), event)


def test_empty_or_unavailable_pr_history_keeps_the_full_checks(pr_checkout, tmp_path):
    assert not check_scope(pr_checkout({}))
    shallow = tmp_path / "shallow"
    shallow.mkdir()
    git(shallow, "init", "-b", "main")
    assert not check_scope(shallow)


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


@pytest.mark.parametrize("job", ["scope", "checks", "package", "browser", "test"])
def test_each_ci_job_has_a_bounded_budget_including_setup(job):
    budget = ci_workflow()["jobs"][job].get("timeout-minutes")
    assert isinstance(budget, int) and 0 < budget <= 30


def test_ci_actions_are_bound_to_immutable_commits():
    for job in ci_workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[a-f0-9]{40}", step["uses"])


def test_only_documentation_uses_one_test_partition():
    workflow = ci_workflow()
    result = subprocess.run(
        ["node", str(ROOT / "tests/evaluate_ci_expressions.mjs")],
        input=json.dumps({"policy": workflow["concurrency"],
                          "partitions": workflow["jobs"]["checks"]["strategy"]["matrix"]["partition"],
                          "contexts": [{"docs_only": value} for value in ["true", "false", "", "unknown"]]}),
        text=True, capture_output=True, check=True,
    )
    assert [row["partitions"] for row in json.loads(result.stdout)] == [
        ["harness"], ["harness", "benchmarks"], ["harness", "benchmarks"], ["harness", "benchmarks"]]


def test_python_partitions_cover_existing_and_future_test_directories_once(tmp_path):
    for name in ["test_shared.py", "benchmarks/test_scorer.py", "future_track/test_new.py"]:
        path = tmp_path / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_example(): pass\n", encoding="utf-8")
    selected = []
    for step in ci_workflow()["jobs"]["checks"]["steps"]:
        command = step.get("run", "")
        if not command.startswith(("make ci-tests ", "make test-python ")):
            continue
        assignments = dict(token.split("=", 1) for token in shlex.split(command) if "=" in token)
        paths = shlex.split(assignments.get("TEST_PATHS", "tests"))
        exclusions = [token for token in shlex.split(assignments.get("PYTEST_ARGS", ""))
                      if token.startswith("--ignore=")]
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths, *exclusions],
            cwd=tmp_path, text=True, capture_output=True, check=True,
        )
        selected.append({line for line in result.stdout.splitlines() if "::test_example" in line})
    assert len(selected) == 2
    assert selected[0].isdisjoint(selected[1])
    assert set.union(*selected) == {f"tests/{name}::test_example" for name in [
        "test_shared.py", "benchmarks/test_scorer.py", "future_track/test_new.py"]}
