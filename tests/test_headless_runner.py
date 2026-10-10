"""Headless cohort selection and identities, with synthetic providers only."""

import hashlib
import json
import sys

import pytest

from harness.web_fetch import headless_runner as runner
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider


@pytest.mark.parametrize("damage", [None, "duplicate", "missing", "output-hash", "diagnostic"])
def test_evidence_checker_uses_explicit_custom_population(tmp_path, damage):
    cases = [
        {
            "id": "fixture",
            "name": "Fixture",
            "url": "https://example.invalid/",
            "category": "web",
            "expected_any": ["fixture"],
        }
    ]
    methods = ["future-a", "future-b"]
    comparison = {
        "kind": "headless_markdown",
        "timeout_seconds": 30,
        "runs_per_case": 1,
        "conversion": "synthetic",
        "concurrency": 1,
        "reference_binding_mode": None,
        "publication_state": "reference_verified",
        "unverified_reference_cases": [],
        "require_independent_result_review": False,
    }
    if damage == "diagnostic":
        comparison["publication_state"] = "diagnostic"
    dataset = {
        "id": "custom",
        "name": "Custom",
        "tool": "web_fetch",
        "cases": cases,
        "coverage_plan": {"web": methods},
        "comparison": comparison,
    }
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps(dataset))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "dataset.json").write_text(json.dumps(dataset))
    (evidence / "output.txt").write_text("fixture")
    rows = [
        {
            "provider": name,
            "case_id": "fixture",
            "attempt_id": name,
            "ok": True,
            "artifact_path": "output.txt",
            "provider_metadata": {},
            "metrics": {"content_sha256": hashlib.sha256(b"fixture").hexdigest()},
        }
        for name in methods
    ]
    if damage == "duplicate":
        rows[1] = rows[0]
    elif damage == "missing":
        rows.pop()
    elif damage == "output-hash":
        rows[0]["metrics"]["content_sha256"] = "0" * 64
    (evidence / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (evidence / "execution.json").write_text(
        json.dumps({"batches": [{"attempt_ids": [row["attempt_id"] for row in rows]}]})
    )
    config, provenance, profile = [
        tmp_path / name for name in ("methods.json", "provenance.json", "profile.json")
    ]
    config.write_text(json.dumps({"providers": {name: {} for name in methods}}))
    provenance.write_text(json.dumps({"implementations": []}))
    profile.write_text(
        json.dumps(
            {
                **comparison,
                "comparison_kind": comparison["kind"],
                "dataset_id": "custom",
                "case_count": 1,
                "providers": methods,
                "case_ids_sha256": hashlib.sha256(b'["fixture"]').hexdigest(),
            }
        )
    )
    args = [
        str(evidence),
        "--canonical-dataset",
        str(canonical),
        "--provider-config",
        str(config),
        "--provenance",
        str(provenance),
        "--report-profile",
        str(profile),
    ]
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    if damage:
        with pytest.raises(ValueError):
            runner.main_evidence_check(args)
    else:
        runner.main_evidence_check(args)
    assert all(path.read_bytes() == raw for path, raw in before.items())


class SyntheticBrowser(Provider):
    calls = []

    def implementation_identity(self, *, refresh=False):
        self._implementation_identity = {
            "binary_sha256": "a" * 64,
            "reported_version": self.config.get("observed_version", "Future 9.2.0"),
        }
        return dict(self._implementation_identity)

    def fetch(self, case, timeout):
        self.calls.append((self.name, case.id, self.config.copy()))
        return ProviderResult(
            True,
            "fixture",
            metadata={
                "binary_sha256": "a" * 64,
                "browser_version": self.implementation_identity()["reported_version"],
            },
        )


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    dataset = tmp_path / "custom-tasks.json"
    dataset.write_text(
        json.dumps(
            {
                "id": "custom",
                "name": "Custom tasks",
                "tool": "web_fetch",
                "cases": [
                    {
                        "id": "fixture",
                        "name": "Fixture",
                        "url": "https://example.invalid/",
                        "category": "web",
                        "expected_any": ["fixture"],
                    }
                ],
            }
        )
    )
    config = tmp_path / "custom-methods.json"
    output = tmp_path / "result"
    SyntheticBrowser.calls = []
    monkeypatch.setattr(runner, "build_provider", SyntheticBrowser)
    monkeypatch.setattr(runner, "load_runtime_env", lambda *args: None)

    def run(payload, *extra):
        config.write_text(json.dumps(payload))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "runner",
                "--dataset",
                str(dataset),
                "--config",
                str(config),
                "--output",
                str(output),
                *extra,
            ],
        )
        runner.main()
        return json.loads((output / "dataset.json").read_text()), json.loads(
            (output / "execution.json").read_text()
        )["batches"][0]

    return run, dataset, config, output


def test_custom_registry_defaults_to_its_methods_and_retains_true_input_identity(experiment):
    run, dataset, config, _ = experiment
    methods = {
        name: {"type": "headless_markdown", "expected_version": "9.2.0"}
        for name in ("future-a", "future-b", "future-c")
    }
    selected, manifest = run({"providers": methods})
    assert [call[0] for call in SyntheticBrowser.calls] == list(methods)
    assert selected["coverage_plan"] == {"web": list(methods)}
    assert "五种" not in selected["name"]
    assert manifest["dataset"]["source"] == dataset.name
    assert manifest["provider_config_source"] == config.name


def test_headless_run_copies_references_only_for_selected_tasks(experiment):
    run, dataset, _, output = experiment
    files = {
        "sources/fixture/reference.txt": b"fixture source",
        "list-references/fixture.json": b'{"case_id":"fixture"}',
        "sources/other/reference.txt": b"other task source",
        "list-references/other.json": b'{"case_id":"other"}',
        "sources/internal-note.txt": b"synthetic private note",
    }
    for relative, raw in files.items():
        path = dataset.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    run({"providers": {"future": {"type": "headless_markdown", "expected_version": "9.2.0"}}})
    for relative, raw in files.items():
        if "/fixture" in relative:
            assert (output / relative).read_bytes() == raw
        else:
            assert not (output / relative).exists()


@pytest.mark.parametrize("tree", ["sources", "list-references"])
def test_headless_rejects_linked_reference_inputs_before_browser_calls(experiment, tree):
    run, dataset, _, output = experiment
    folder = dataset.parent / tree
    folder.mkdir()
    actual = dataset.parent / "same-source.txt"
    actual.write_text("synthetic source")
    (folder / "linked").symlink_to(actual)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        run({"providers": {"future": {"type": "headless_markdown", "expected_version": "9.2.0"}}})
    assert not SyntheticBrowser.calls
    assert not output.exists()


def test_resource_cohort_overrides_normal_version_and_binary_without_provider_name_rules(
    experiment,
):
    run, _, _, _ = experiment
    payload = {
        "providers": {
            "moli": {
                "type": "headless_markdown",
                "expected_version": "1.1.5",
                "observed_version": "moli 1.1.1",
            }
        },
        "default_cohort": "normal",
        "resource_cohort": "resource",
        "cohorts": {
            "normal": {"providers": ["moli"]},
            "resource": {
                "providers": ["moli"],
                "resource_container_image": "fixture:local",
                "provider_overrides": {
                    "moli": {"expected_version": "1.1.1", "resource_binary": "/fixture/moli"}
                },
            },
        },
    }
    selected, _ = run(payload, "--resource-sample-interval-ms", "50")
    assert SyntheticBrowser.calls[0][2]["resource_binary"] == "/fixture/moli"
    assert selected["comparison"]["cohort"] == "resource"


def test_configured_version_mismatch_fails_before_calls_or_output(experiment):
    run, _, _, output = experiment
    with pytest.raises(ValueError, match="expected version"):
        run(
            {"providers": {"future-a": {"type": "headless_markdown", "expected_version": "9.3.0"}}},
            "--providers",
            "future-a",
        )
    assert SyntheticBrowser.calls == []
    assert not output.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ("--providers", "unknown"),
        ("--providers", "future-a,future-a"),
        ("--providers", "future-a,"),
        ("--providers", ""),
        ("--cohort", "unknown"),
        ("--cohort", ""),
        ("--case-ids", "fixture,unknown"),
        ("--case-ids", "fixture,fixture"),
        ("--case-ids", ""),
        ("--tracks", "web,unknown"),
        ("--tracks", ""),
    ],
)
def test_invalid_scope_is_rejected_before_any_calls_or_output(experiment, extra):
    run, _, _, output = experiment
    with pytest.raises((ValueError, SystemExit)):
        run({"providers": {"future-a": {"type": "headless_markdown"}}}, *extra)
    assert SyntheticBrowser.calls == []
    assert not output.exists()


def test_declared_cohort_and_explicit_selection_use_the_same_registry(experiment):
    run, _, _, _ = experiment
    payload = {
        "providers": {name: {"type": "headless_markdown"} for name in ("first", "second", "extra")},
        "default_cohort": "pair",
        "cohorts": {"pair": {"providers": ["first", "second"]}},
    }
    selected, _ = run(payload, "--providers", " extra , first ")
    assert selected["coverage_plan"] == {"web": ["extra", "first"]}
    assert [call[0] for call in SyntheticBrowser.calls] == ["extra", "first"]


def test_changed_executable_is_rejected_after_calls_and_keeps_physical_evidence(
    experiment, monkeypatch
):
    run, _, _, output = experiment
    original = SyntheticBrowser.implementation_identity

    def identity(self, *, refresh=False):
        result = original(self, refresh=refresh)
        if refresh:
            result["binary_sha256"] = "b" * 64
        return result

    monkeypatch.setattr(SyntheticBrowser, "implementation_identity", identity)
    with pytest.raises(ValueError, match="identity changed during execution"):
        run({"providers": {"future-a": {"type": "headless_markdown"}}})
    assert len(SyntheticBrowser.calls) == 1
    assert (output / "results.jsonl").is_file()
    assert not (output / "execution.json").exists()


def test_resource_mode_requires_an_explicit_image_before_calls(experiment):
    run, _, _, output = experiment
    with pytest.raises(SystemExit):
        run(
            {"providers": {"future-a": {"type": "headless_markdown"}}},
            "--resource-sample-interval-ms",
            "50",
        )
    assert SyntheticBrowser.calls == []
    assert not output.exists()


def test_shipped_main_and_resource_cohorts_validate_their_own_release_pins():
    from harness.web_fetch.config import load_provider_settings

    settings = load_provider_settings(runner.ROOT / "configs/providers.headless.json")
    _, _, normal = runner.select_cohort(settings)
    _, resource, isolated = runner.select_cohort(settings, resource=True)
    assert resource["resource_container_image"]
    assert all(config.get("resource_binary") for config in isolated.values())
    runner.require_expected_provider_versions({"moli": {"reported_version": "moli 1.1.5"}}, normal)
    runner.require_expected_provider_versions(
        {"moli": {"reported_version": "moli 1.1.1"}}, isolated
    )
    with pytest.raises(ValueError, match="expected version"):
        runner.require_expected_provider_versions(
            {"moli": {"reported_version": "moli 1.1.5"}}, isolated
        )


def test_native_command_runs_a_custom_binary_and_preserves_its_identity(tmp_path):
    import hashlib
    import subprocess

    binary = tmp_path / "synthetic-browser"
    binary.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
        '  printf "fixture-browser 7.4.0\\n"\nelse\n'
        '  printf "Independent fixture output\\n"\nfi\n'
    )
    binary.chmod(0o700)
    dataset = tmp_path / "tasks.json"
    dataset.write_text(
        json.dumps(
            {
                "id": "fixture",
                "tool": "web_fetch",
                "cases": [
                    {
                        "id": "one",
                        "name": "One",
                        "url": "https://example.invalid/",
                        "category": "web",
                        "expected_any": ["Independent fixture output"],
                    }
                ],
            }
        )
    )
    config = tmp_path / "methods.json"
    config.write_text(
        json.dumps(
            {
                "providers": {
                    "custom-browser": {
                        "type": "headless_markdown",
                        "engine": "moli",
                        "binary": str(binary),
                        "expected_version": "7.4.0",
                    }
                }
            }
        )
    )
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    output = tmp_path / "measured"
    completed = subprocess.run(
        [
            sys.executable,
            str(runner.ROOT / "scripts/run_headless_benchmark.py"),
            "--dataset",
            str(dataset),
            "--config",
            str(config),
            "--output",
            str(output),
            "--env-file",
            str(env_file),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["ok"]
    assert records[0]["metrics"]["content_pass"]
    manifest = json.loads((output / "execution.json").read_text())["batches"][0]
    identity = manifest["providers"][0]["implementation_identity"]
    assert identity["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert identity["reported_version"] == "fixture-browser 7.4.0"
    assert identity["call_metadata_coverage"]["binary_sha256"] == 1


def test_generic_provider_validation_enforces_the_same_declared_version(tmp_path):
    from harness.web_fetch.providers.headless import HeadlessProvider

    binary = tmp_path / "synthetic-browser"
    binary.write_text('#!/bin/sh\nprintf "fixture-browser 7.4.0\\n"\n')
    binary.chmod(0o700)
    for expected, accepted in [("7.4.0", True), ("7.5.0", False)]:
        provider = HeadlessProvider(
            "arbitrary-label",
            {
                "type": "headless_markdown",
                "engine": "moli",
                "binary": str(binary),
                "expected_version": expected,
            },
        )
        errors = provider.validate()
        assert (not errors) == accepted
        if not accepted:
            assert "expected version 7.5.0, reported 7.4.0" in errors[0]


@pytest.mark.parametrize('state', ['synthetic_example_not_for_release', 'diagnostic'])
def test_browser_cohort_inherits_source_eligibility(experiment, state):
    from harness.web_fetch.reference_lifecycle import require_certifiable_evidence
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.report_package import build

    run, path, _, output = experiment
    dataset = json.loads(path.read_text())
    comparison = {'publication_state': state, 'evidence_state': 'diagnostic',
                  'unqualified_reference_cases': ['fixture'],
                  'source_qualification': {'status': 'not_qualified', 'sha256': 'a' * 64}}
    dataset['comparison'] = comparison
    path.write_text(json.dumps(dataset))
    selected, manifest = run({'providers': {'future': {'type': 'headless_markdown',
                                                       'expected_version': '9.2.0'}}})
    for key, value in comparison.items():
        assert selected['comparison'][key] == value
    assert manifest['evidence_state'] == 'diagnostic'
    with pytest.raises(ValueError, match='Diagnostic'):
        require_certifiable_evidence(selected, [manifest])
    with pytest.raises(ValueError, match='Diagnostic'):
        build_report(output, output / 'report')
    with pytest.raises(ValueError, match='Diagnostic'):
        build(output, output.parent / 'release.zip')


@pytest.mark.parametrize('speed_selection', [False, True])
def test_synthetic_inputs_remain_non_release_after_selection_and_native_calls(tmp_path, speed_selection):
    import subprocess
    from harness.web_fetch.reference_lifecycle import require_certifiable_evidence
    from harness.web_fetch.report_package import build

    dataset = json.loads((runner.ROOT / 'harness/web_fetch/data/fixture-dataset.json').read_text())
    original = dataset['cases'][0]
    dataset['cases'] = [{**original, 'id': f'page-{index}',
                         'url': f'https://host-{index}.invalid/'} for index in range(5)]
    tasks = tmp_path / 'tasks.json'
    tasks.write_text(json.dumps(dataset))
    if speed_selection:
        selected = tmp_path / 'selected'
        result = subprocess.run([sys.executable, '-c',
            'from harness.web_fetch.speed_cohort import selection_main; selection_main()',
            '--dataset', str(tasks), '--output', str(selected), '--count', '5'],
            text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        tasks = selected / 'dataset.json'
        assert json.loads(tasks.read_text())['comparison']['publication_state'] == (
            dataset['comparison']['publication_state'])
    binary = tmp_path / 'browser'
    binary.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
                      'echo "fixture-browser 7.4.0"\nelse\n'
                      'echo "Expected fixture content"\nfi\n')
    binary.chmod(0o700)
    methods = tmp_path / 'methods.json'
    methods.write_text(json.dumps({'providers': {'native': {'type': 'headless_markdown',
        'engine': 'moli', 'binary': str(binary), 'expected_version': '7.4.0'}}}))
    env = tmp_path / 'empty.env'
    env.write_text('')
    output = tmp_path / 'measured'
    result = subprocess.run([sys.executable, '-c',
        'from harness.web_fetch.headless_runner import main; main()', '--dataset', str(tasks),
        '--config', str(methods), '--env-file', str(env), '--output', str(output)],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads((output / 'dataset.json').read_text())
    assert saved['comparison']['publication_state'] == dataset['comparison']['publication_state']
    assert len((output / 'results.jsonl').read_text().splitlines()) == 5
    with pytest.raises(ValueError, match='Diagnostic'):
        require_certifiable_evidence(saved)
    with pytest.raises(ValueError, match='Diagnostic'):
        build(output, tmp_path / 'published.zip')
    assert not (tmp_path / 'published.zip').exists()
