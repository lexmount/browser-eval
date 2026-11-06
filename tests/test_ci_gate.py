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
        ["primary"], ["primary", "secondary"], ["primary", "secondary"], ["primary", "secondary"]]


def test_python_partitions_cover_existing_and_future_test_directories_once(tmp_path):
    names = ["test_shared.py", "benchmarks/test_scorer.py", "future_track/test_new.py",
             "test_browser_observation_contracts.py", "test_report_browser_interactions.py",
             "web_search/test_visual.py"]
    for name in names:
        path = tmp_path / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"def test_example_{i}(): pass\n" for i in range(10)), encoding="utf-8")
    selected = []
    commands = [step['run'] for step in ci_workflow()["jobs"]["checks"]["steps"]
                if step.get('run', '').startswith(("make ci-tests ", "make test-python "))]
    for command in [*commands, 'make browser-check']:
        dry_run = subprocess.run(
            [*shlex.split(command)[:2], "-n", *shlex.split(command)[2:]],
            cwd=ROOT, text=True, capture_output=True, check=True,
        )
        python = next(line for line in dry_run.stdout.splitlines() if ' -m pytest ' in line)
        arguments = iter(shlex.split(python)[shlex.split(python).index('pytest') + 1:])
        options = []
        for argument in arguments:
            if argument in {'-n', '--dist'}:
                next(arguments)
            else:
                options.append(argument)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "tests.conftest", "--collect-only", *options],
            cwd=tmp_path, text=True, capture_output=True, check=True,
            env={**os.environ, 'PYTHONPATH': str(ROOT)},
        )
        selected.append({line for line in result.stdout.splitlines() if "::test_example" in line})
    assert len(selected) == 3
    for index, population in enumerate(selected):
        assert all(population.isdisjoint(other) for other in selected[index + 1:])
    assert set.union(*selected) == {f"tests/{name}::test_example_{i}"
                                   for name in names for i in range(10)}


def test_ci_browser_exclusion_reaches_recursive_make(tmp_path):
    makefile = tmp_path / 'Makefile'
    targets = ['runtime-check', 'track-naming-check', 'lint-check', 'shell-check',
               'fixture-smoke', 'test-ui', 'test-native-adapters']
    makefile.write_text(
        f'include {ROOT / "Makefile"}\n'
        + ''.join(f'{target}:\n\t@:\n' for target in targets)
        + 'test-python:\n\t@echo browser-selection=$(BROWSER_TESTS)\n'
        + f'ci-tests:\n\t+$(MAKE) --no-print-directory -f {makefile} test-python\n',
        encoding='utf-8',
    )
    result = subprocess.run(['make', '--no-print-directory', '-f', str(makefile), 'ci-tests'],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.count('browser-selection=exclude') == 2
    assert 'browser-selection=include' not in result.stdout


def test_new_release_targets_inherit_fresh_validation_automatically(tmp_path):
    makefile = tmp_path / 'Makefile'
    makefile.write_text(f'include {ROOT / "Makefile"}\n'
                        'future-release-check: nested-validation\n'
                        'nested-validation:\n\t@echo full-validation=$$BROWSER_EVAL_FULL_REPORT_CHECK\n',
                        encoding='utf-8')
    result = subprocess.run(['make', '--no-print-directory', '-f', str(makefile), 'future-release-check'],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'full-validation=1'


@pytest.mark.parametrize('shard', ['0/2', '3/2', '1/0', 'bad', '1/2/3'])
def test_invalid_shard_fails_instead_of_silently_omitting_tests(tmp_path, shard):
    (tmp_path / 'test_example.py').write_text('def test_example(): pass\n', encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-p', 'tests.conftest', '--collect-only', '--ci-shard=' + shard],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(ROOT)}, text=True, capture_output=True,
    )
    assert result.returncode == 4
    assert '--ci-shard requires' in result.stderr
