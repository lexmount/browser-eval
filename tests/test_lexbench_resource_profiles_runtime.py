"""The complete resource cohort freezes inputs before the first physical call."""

import hashlib
import json
from pathlib import Path

import pytest

from harness.lexbench import resource_profiles as profiles


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    # The collector's source is frozen data here; never modify repository code.
    source = tmp_path / "collector.py"
    source.write_bytes(Path(profiles.__file__).read_bytes())
    monkeypatch.setattr(profiles, "__file__", str(source))
    runner, modules = tmp_path / "runner", tmp_path / "modules"
    binary = tmp_path / "browser"
    binary_sha = write(binary, b"synthetic browser")
    tasks = [f"task-{i}" for i in range(557)]
    configs = {
        f"engine_{i}": {
            "layout": "on" if i % 2 else "off",
            "target": "aarch64-unknown-linux-gnu",
            "profile": "release",
            "version": "fixture",
            "binary": str(binary),
            "sha256": binary_sha,
        }
        for i in range(4)
    }
    protocol = {
        "schema": "moli_linux_resource_profiles/1",
        "id": "fixture",
        "configs": configs,
        "task_ids": tasks,
        "attempts": 5,
        "jobs": 1,
        "sample_interval_ms": 250,
        "memory_metric": "process_tree_pss",
        "orchestrator_sha256": profiles.sha256(source),
        "order": [[m, "baseline"] for m in configs] + [[m, "engine"] for m in reversed(configs)],
        "runner_root": str(runner),
        "node_modules": str(modules),
        "seed": "fixed-seed",
        "image": "sha256:" + "a" * 64,
        "max_observer_effect_pct": 10,
        "input_files": {"manifest.json": write(runner / "manifest.json", {"tasks": tasks})},
        "node_modules_lock": str(modules / ".package-lock.json"),
        "node_modules_lock_sha256": write(modules / ".package-lock.json", {"fixture": True}),
        "agent_browser_binary": str(tmp_path / "agent"),
        "agent_browser_sha256": write(tmp_path / "agent", b"synthetic agent"),
        "chart_keys": [
            {"task_id": task, "attempt": attempt} for task in tasks[:209] for attempt in range(1, 6)
        ],
    }
    protocol_path = tmp_path / "protocol.json"
    write(protocol_path, protocol)
    return protocol_path, protocol, source


def fake_docker(monkeypatch, protocol, *, after=None):
    calls = []

    def run(command, *, stdout, stderr, check):
        assert check
        run_id = command[command.index("--run-id") + 1]
        phase = command[command.index("--resource-profile") + 1]
        method = run_id.removesuffix("_" + phase)
        mount = next(
            c for c in command if c.startswith("type=bind,src=") and c.endswith(",dst=/results")
        )
        output = Path(mount.removeprefix("type=bind,src=").removesuffix(",dst=/results"))
        assert output.is_absolute(), "Docker bind source must be absolute"
        directory = output / run_id
        directory.mkdir()
        config = protocol["configs"][method]
        rows = [
            {
                "task_id": task,
                "attempt": attempt,
                "engine_provenance": {
                    "binary_sha256": config["sha256"],
                    "layout_enabled": config["layout"] == "on",
                },
                "resource": {"pss_peak_bytes": 1024, "sample_interval_ms": 250},
            }
            for task in protocol["task_ids"]
            for attempt in range(1, 6)
        ]
        write(directory / "run_manifest.json", {"completion_status": "completed"})
        write(directory / "host_summary.json", {"polluted": False})
        (directory / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        calls.append((method, phase))
        if after:
            after(len(calls))

    monkeypatch.setattr(profiles.subprocess, "run", run)
    return calls


def test_full_cohort_retains_exact_frozen_sources_and_hash_bound_calls(
    experiment, tmp_path, monkeypatch
):
    path, protocol, source = experiment
    frozen = path.read_bytes()
    calls = fake_docker(monkeypatch, protocol)
    output = tmp_path / "output"
    profiles.execute(path, output)
    assert calls == [tuple(item) for item in protocol["order"]]
    assert (output / "protocol.json").read_bytes() == frozen
    assert (output / "orchestrator.py").read_bytes() == source.read_bytes()
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["protocol_sha256"] == hashlib.sha256(frozen).hexdigest()
    assert receipt["orchestrator_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(receipt["results"]) == 8
    for binding in receipt["results"].values():
        assert binding["rows"] == 2785
        for file, key in [
            ("run_manifest.json", "run_manifest_sha256"),
            ("results.jsonl", "results_sha256"),
            ("host_summary.json", "host_summary_sha256"),
        ]:
            assert profiles.sha256(output / binding["run_id"] / file) == binding[key]


def test_changed_protocol_stops_before_the_next_call(experiment, tmp_path, monkeypatch):
    path, protocol, _ = experiment
    original = path.read_bytes()
    calls = fake_docker(
        monkeypatch,
        protocol,
        after=lambda count: path.write_bytes(original + b"\n") if count == 1 else None,
    )
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="protocol changed"):
        profiles.execute(path, output)
    assert len(calls) == 1
    assert (output / "protocol.json").read_bytes() == original
    assert not (output / "receipt.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.pop("max_observer_effect_pct"),
        lambda p: p.update(chart_keys=p["chart_keys"][:-1]),
        lambda p: p["configs"]["engine_0"].update(sha256="g" * 64),
        lambda p: p.update(task_ids=["../outside", *p["task_ids"][1:]]),
    ],
)
def test_invalid_predeclared_contract_never_starts_a_call(
    experiment, tmp_path, monkeypatch, change
):
    path, protocol, _ = experiment
    change(protocol)
    write(path, protocol)
    calls = fake_docker(monkeypatch, protocol)
    output = tmp_path / "output"
    with pytest.raises(ValueError):
        profiles.execute(path, output)
    assert calls == []
    assert not output.exists()


def test_parent_input_binding_is_rejected_before_execution(experiment, tmp_path, monkeypatch):
    path, protocol, _ = experiment
    protocol["input_files"]["../outside.json"] = write(
        tmp_path / "outside.json", {"not": "a runner input"}
    )
    write(path, protocol)
    calls = fake_docker(monkeypatch, protocol)
    with pytest.raises(ValueError, match="relative"):
        profiles.execute(path, tmp_path / "output")
    assert calls == []


def test_linked_manifest_is_rejected_before_reading_its_contents(experiment, tmp_path, monkeypatch):
    path, protocol, _ = experiment
    output = tmp_path / "output"
    external = tmp_path / "external.json"
    write(external, {"completion_status": "completed"})

    def replace_manifest(count):
        if count == 1:
            manifest = output / "engine_0_baseline/run_manifest.json"
            manifest.unlink()
            manifest.symlink_to(external)

    calls = fake_docker(monkeypatch, protocol, after=replace_manifest)
    original = Path.read_text

    def reject_link_read(file, *args, **kwargs):
        if file.is_symlink():
            raise RuntimeError("Read through a symlink before rejecting it")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_link_read)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        profiles.execute(path, output)
    assert len(calls) == 1
    assert not (output / "receipt.json").exists()


@pytest.mark.parametrize(
    "name", ["engine_1_baseline.log", "engine_1_baseline-command.json", "engine_1-active-set.json"]
)
def test_linked_control_files_cannot_overwrite_external_files(
    experiment, tmp_path, monkeypatch, name
):
    path, protocol, _ = experiment
    output = tmp_path / "output"
    external = tmp_path / "external.json"
    external.write_text("external content")

    def inject_link(count):
        if count == 1:
            (output / name).symlink_to(external)

    calls = fake_docker(monkeypatch, protocol, after=inject_link)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        profiles.execute(path, output)
    assert len(calls) == 1
    assert external.read_text() == "external content"
    assert not (output / "receipt.json").exists()


def test_source_mutation_stops_the_queue_and_preserves_the_frozen_copy(
    experiment, tmp_path, monkeypatch
):
    path, protocol, source = experiment
    frozen_source = source.read_bytes()
    calls = fake_docker(
        monkeypatch,
        protocol,
        after=lambda count: source.write_bytes(b"changed") if count == 1 else None,
    )
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="orchestrator changed"):
        profiles.execute(path, output)
    assert len(calls) == 1
    assert (output / "orchestrator.py").read_bytes() == frozen_source
    assert not (output / "receipt.json").exists()


def test_relative_cli_output_is_normalized_for_docker_mounts(experiment, tmp_path, monkeypatch):
    path, protocol, _ = experiment
    monkeypatch.chdir(tmp_path)
    calls = fake_docker(monkeypatch, protocol)
    profiles.execute(path, Path("relative-output"))
    assert len(calls) == 8
    assert (tmp_path / "relative-output/receipt.json").is_file()
